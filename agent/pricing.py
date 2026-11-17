"""Ceiling learner + k* optimiser (pure, stdlib only).

k = price_per_kg / TV.  A buyer drops offers with k above its SECRET ceiling (ABOVE_LIMIT), then fills the rest
cheapest-k first (OUTBID when the RFQ is full before our turn). So the chance an offer sells at k is

    P(k) = P_spec x P(ceiling >= k) x P(we rank inside the filled bags at k)
           (spec risk)  (ceiling-bound)   (competition-bound)

and the value of one bag offered at k, compared with keeping it, is

    EV(k) = (k - V) x TV x 60 x P(k)

where V is what the bag is worth if we DON'T sell it now (0.80 in the last round = salvage; higher earlier,
because it can still be sold in later rounds - see planner.reservation_k).  k* = argmax EV(k).

Evidence used (per buyer NAME, pooled per buyer TYPE):
  ACCEPTED / PARTIAL / OUTBID at k  -> that RFQ's ceiling >= k         (our offer passed the ceiling)
  ABOVE_LIMIT at k                  -> that RFQ's ceiling <  k
  market_summary price_max          -> ceiling >= ~price_max / TV_ref  (someone sold there)
  RFQ fully bought                  -> competition-bound: clearing k ~ price_max / TV_ref, OUTBID k is above it
  RFQ under-filled                  -> ceiling-bound: competition did not bite, the ceiling did

Ceilings differ PER RFQ even for the same buyer (slide data), so evidence is widened by JITTER before use.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

SALVAGE = 0.80
PASSED = {"ACCEPTED", "PARTIAL", "OUTBID"}
TYPE_ALIASES = {"COM": "COMMERCIAL", "INS": "INSTANT", "SPEC": "SPECIALTY", "SPE": "SPECIALTY"}

# Prior ceiling range [lo, hi] per buyer type, BEFORE any evidence.  Based on the R1 slide example
# (COM 1.06 passed / 1.25 cut, INS 0.97 both passed and cut) and the organiser's local market model.
# These are assumptions - edit them when you learn more.
PRIORS = {
    "COMMERCIAL": (1.02, 1.12),
    "INSTANT": (0.93, 1.01),
    "SPECIALTY": (1.08, 1.22),
}
# Optional per-buyer-NAME priors from agent/priors.json (not included in this repo).
# The competition seed may differ, so these are soft starting points; real results override them.
BUYER_PRIORS: dict[str, tuple[float, float]] = {}


def load_priors(path=None) -> None:
    """Load agent/priors.json if present (type bands + per-buyer bands)."""
    import json
    from pathlib import Path
    p = Path(path) if path else Path(__file__).resolve().parent / "priors.json"
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    for t, band in (data.get("types") or {}).items():
        PRIORS[t] = (band["lo"], band["hi"])
    for name, b in (data.get("buyers") or {}).items():
        BUYER_PRIORS[name] = (b["lo"], b["hi"])


load_priors()
DEFAULT_PRIOR = (0.90, 1.10)
JITTER = 0.025          # how much one buyer's ceiling moves between its RFQs
COMP_WIDTH = 0.02       # softness of the competition cut-off around the clearing k
FESTIVAL_BONUS = 0.02   # assumed ceiling lift in calendar "festival" rounds (unknown - keep small)


def norm_type(t: str | None) -> str:
    t = (t or "").strip().upper()
    return TYPE_ALIASES.get(t, t)


@dataclass
class Belief:
    """What we believe about one RFQ's ceiling and competition, in k units."""
    lo: float                     # ceiling >= lo (almost surely)
    hi: float                     # ceiling <= hi (almost surely)
    clear_k: float | None = None  # estimated competition clearing k (None = ceiling-bound / unknown)
    width: float = COMP_WIDTH
    source: str = "prior"
    comp_weight: float = 1.0      # chance this RFQ is competition-bound (busy). 1 - w = quiet, sells up to ceiling

    def p_ceiling(self, k: float) -> float:
        """P(ceiling >= k), ceiling ~ Uniform(lo, hi)."""
        if k <= self.lo:
            return 1.0
        if k > self.hi:
            return 0.0
        return (self.hi - k) / (self.hi - self.lo) if self.hi > self.lo else 1.0

    def p_compete(self, k: float) -> float:
        """P(our k is low enough to be filled before the RFQ runs out)."""
        if self.clear_k is None:
            return 1.0
        z = (k - self.clear_k) / max(self.width, 1e-6)
        busy = 1 / (1 + math.exp(max(-50.0, min(50.0, z))))
        # practice data: busy RFQs (>=8 offers) cleared low, quiet ones sold near the ceiling -> a mixture
        return (1 - self.comp_weight) + self.comp_weight * busy

    def p_accept(self, k: float) -> float:
        return self.p_ceiling(k) * self.p_compete(k)


@dataclass
class Evidence:
    passed: list = field(default_factory=list)   # k that passed the ceiling
    above: list = field(default_factory=list)    # k that were ABOVE_LIMIT
    clear: list = field(default_factory=list)    # clearing-k estimates from fully filled RFQs
    outbid: list = field(default_factory=list)   # our OUTBID k (clearing was below)
    fills: list = field(default_factory=list)    # bought/wanted ratios

    def as_dict(self):
        return {"passed": self.passed, "above": self.above, "clear": self.clear,
                "outbid": self.outbid, "fills": self.fills}


class CeilingLearner:
    """Collects evidence round by round and turns it into a Belief for any RFQ."""

    def __init__(self, priors: dict | None = None, jitter: float = JITTER, festival_bonus: float = FESTIVAL_BONUS,
                 buyer_priors: dict | None = None):
        self.priors = {**PRIORS, **(priors or {})}
        self.buyer_priors = dict(BUYER_PRIORS if buyer_priors is None else buyer_priors)
        self.jitter = jitter
        self.festival_bonus = festival_bonus
        self.by_name: dict[str, Evidence] = {}
        self.by_type: dict[str, Evidence] = {}
        self.seen_rounds: set = set()

    # ------------------------------------------------------------------ learning
    def _ev(self, name: str, btype: str) -> tuple[Evidence, Evidence]:
        return self.by_name.setdefault(name, Evidence()), self.by_type.setdefault(norm_type(btype), Evidence())

    def observe(self, results: dict, tv: dict, lot_samples: dict | None = None, specs: dict | None = None,
                round_id: str | None = None) -> None:
        """Learn from one GET /v1/results (or state.last_results) payload.

        results: {"round_id", "my_offers": [{rfq_id, lot_id, price_per_kg, status, filled_kg, kg}],
                  "market_summary": [{rfq_id, buyer, buyer_type, wanted_kg, bought_kg, offers, price_max ...}]}
        tv: lot_id -> our TV estimate (used to turn prices into k).
        lot_samples/specs (optional): to pick a reference TV for market prices = the best of OUR lots that
        pass that RFQ's spec (other teams' lots are similar; conservative because it gives the lowest k).
        """
        rid = round_id or results.get("round_id")
        if rid is not None:
            if rid in self.seen_rounds:
                return
            self.seen_rounds.add(rid)
        summary = {row["rfq_id"]: row for row in results.get("market_summary", [])}
        for o in results.get("my_offers", []):
            row = summary.get(o["rfq_id"], {})
            name, btype = row.get("buyer", o.get("buyer", "?")), row.get("buyer_type", o.get("buyer_type", "?"))
            if o["lot_id"] not in tv:
                continue
            k = o["price_per_kg"] / tv[o["lot_id"]]
            en, et = self._ev(name, btype)
            if o["status"] in PASSED:
                en.passed.append(k)
                et.passed.append(k)
            elif o["status"] == "ABOVE_LIMIT":
                en.above.append(k)
                et.above.append(k)
            if o["status"] == "OUTBID":
                en.outbid.append(k)
                et.outbid.append(k)
        for rfq_id, row in summary.items():
            en, et = self._ev(row.get("buyer", "?"), row.get("buyer_type", "?"))
            wanted, bought = row.get("wanted_kg") or 0, row.get("bought_kg") or 0
            ratio = bought / wanted if wanted else 0.0
            en.fills.append(ratio)
            et.fills.append(ratio)
            ref = self._ref_tv(rfq_id, tv, lot_samples, specs)
            if ref and row.get("price_max"):
                k_max = row["price_max"] / ref
                en.passed.append(k_max)          # someone sold at ~k_max, so the ceiling was >= it
                et.passed.append(k_max)
                if ratio >= 0.999:               # full -> competition decided who sold
                    en.clear.append(k_max)
                    et.clear.append(k_max)

    @staticmethod
    def _ref_tv(rfq_id, tv, lot_samples, specs):
        if not tv:
            return None
        if lot_samples and specs and rfq_id in specs:
            from market_core import passes
            ok = [tv[l] for l, smp in lot_samples.items() if l in tv and passes(smp, specs[rfq_id])]
            if ok:
                return max(ok)
        return max(tv.values())

    # ------------------------------------------------------------------ beliefs
    def belief(self, buyer: str, buyer_type: str, festival: bool = False) -> Belief:
        btype = norm_type(buyer_type)
        lo, hi = self.priors.get(btype, DEFAULT_PRIOR)
        src = "prior"
        if buyer in self.buyer_priors:                 # practice data for this buyer name
            lo, hi = self.buyer_priors[buyer]
            src = "data"
        j = self.jitter
        en, et = self.by_name.get(buyer), self.by_type.get(btype)
        # type-level evidence is weaker (different buyers of one type have different ceilings)
        for ev, widen, tag in ((et, 2 * j, "type"), (en, j, "buyer")):
            if not ev:
                continue
            if ev.passed:
                lo = max(lo, max(ev.passed) - widen)
                src = tag
            if ev.above:
                hi = min(hi, min(ev.above) + widen)
                src = tag
            if lo >= hi:                       # conflicting evidence -> centre it, keep some width
                mid = (lo + hi) / 2
                lo, hi = mid - j, mid + j
        if festival:
            lo, hi = lo + self.festival_bonus, hi + self.festival_bonus
        clear, weight = self.competition(buyer, btype)
        return Belief(lo=lo, hi=hi, clear_k=clear, source=src, comp_weight=weight)

    def competition(self, buyer: str, buyer_type: str) -> tuple[float | None, float]:
        """(clearing k, share of RFQs that were competition-bound) for this buyer, falling back to its type.

        share = fraction of this buyer's RFQs that were fully bought. clearing k = median of the k levels
        we saw lose the queue (OUTBID) and of sold-out RFQs' top prices.
        """
        for ev in (self.by_name.get(buyer), self.by_type.get(norm_type(buyer_type))):
            if not ev or not ev.fills:
                continue
            share = sum(1 for f in ev.fills if f >= 0.999) / len(ev.fills)
            if share == 0 or not (ev.clear or ev.outbid):
                return None, 0.0
            cands = []
            if ev.clear:                              # sold-out RFQs' top prices: robust median
                pts = sorted(ev.clear)
                cands.append(pts[len(pts) // 2])
            if ev.outbid:                             # the LOWEST k that still lost -> clearing is just below it
                # -0.01 was best on both the practice-log replay and the organiser-engine arena (tuned 17 Nov)
                cands.append(min(ev.outbid) - COMP_WIDTH / 2)
            return min(cands), share
        return None, 0.0

    def clearing_k(self, buyer: str, buyer_type: str) -> float | None:
        """Clearing k when this buyer is mostly competition-bound, else None (ceiling-bound)."""
        clear, share = self.competition(buyer, buyer_type)
        return clear if share >= 0.5 else None

    def state_dict(self) -> dict:
        return {"by_name": {k: v.as_dict() for k, v in self.by_name.items()},
                "by_type": {k: v.as_dict() for k, v in self.by_type.items()}}


# ---------------------------------------------------------------------- k* optimiser

def k_grid(lo: float, hi: float, step: float = 0.0025) -> list[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 6) for i in range(n + 1)]


def best_k(belief: Belief, reserve: float = SALVAGE, floor: float = 0.85, cap: float = 1.40,
           p_spec: float = 1.0, step: float = 0.0025, min_p: float = 0.0) -> dict:
    """Return k* that maximises (k - reserve) x P(accept), plus the curve for plotting.

    floor: never offer below this (safety above the 0.80 salvage).  reserve: V, the hold value.
    min_p: only consider k whose P(accept) >= min_p ("safe" risk); falls back to the plain optimum.
    """
    floor = max(floor, reserve + 1e-9, SALVAGE + 1e-9)
    best = {"k": floor, "p": belief.p_accept(floor) * p_spec, "ev": -1.0}
    free = dict(best)
    curve = []
    for k in k_grid(floor, cap, step):
        p = belief.p_accept(k) * p_spec
        ev = (k - reserve) * p
        curve.append((k, p, ev))
        if ev > free["ev"] + 1e-12:
            free = {"k": k, "p": p, "ev": ev}
        if p >= min_p and ev > best["ev"] + 1e-12:
            best = {"k": k, "p": p, "ev": ev}
    if best["ev"] < 0:
        best = free
    best["curve"] = curve
    return best


def ladder(belief: Belief, bags: int, reserve: float = SALVAGE, floor: float = 0.85, rungs: int = 3,
           spread: float | None = None) -> list[tuple[int, float]]:
    """Split bags across several k (low rung first). Useful to LEARN the ceiling quickly and hedge.

    Returns [(bags, k), ...].  The first rung is k* (most bags), the others step UP by `spread`
    (default: a third of the remaining uncertainty hi-lo) - a rung below k* would only give money away,
    because k* already balances price against the chance of selling.
    """
    k0 = best_k(belief, reserve, floor)["k"]
    if bags <= 1 or rungs <= 1:
        return [(bags, k0)]
    spread = spread if spread is not None else max(0.01, (belief.hi - belief.lo) / 3)
    ks = [round(k0 + spread * i, 4) for i in range(rungs)]
    counts = [1] * len(ks)
    counts[0] += bags - len(ks)
    while counts and sum(counts) > bags:       # fewer bags than rungs
        counts.pop()
        ks.pop()
    return [(c, k) for c, k in zip(counts, ks) if c > 0]


def probe_k(belief: Belief, floor: float = 0.85) -> float:
    """A k in the middle of the unknown range: one bag here tells us which half the ceiling is in."""
    return max(floor, (belief.lo + belief.hi) / 2)
