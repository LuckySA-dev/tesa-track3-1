"""Round planner: which lot -> which RFQ, how many bags, at what k (pure, stdlib only).

Idea (see ../docs/STRATEGY.md):
  * Every bag we sell at k instead of leaving it adds (k - 0.80) x TV x 60 to the PR numerator.
  * A bag not sold now is not lost - it can be sold in a later round. So this round we only sell when
    k beats V, the "hold value" of a bag. V = 0.80 in the last round (salvage) and rises with the number of
    future rounds that can still absorb our stock (planner.reservation_k).
  * For every spec-valid (lot, RFQ) pair: k* = argmax (k - V) x P(accept)   (pricing.best_k)
  * Greedy: give bags to the pair with the highest expected THB uplift per bag first, respecting
    kg_available, RFQ bags, capacity_left_kg.  Optionally add 1-bag "probe" offers to learn ceilings early.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import market_core as mc
from pricing import SALVAGE, Belief, CeilingLearner, best_k, norm_type, probe_k

BAG = mc.BAG_KG


@dataclass
class PlanConfig:
    floor: float = 0.85             # never offer below this k (0.80 = salvage; keep a safety gap)
    cap: float = 1.40               # never offer above this k
    noise: dict | None = None       # sample noise, e.g. {"defect": .35, "moisture": .3} on Day 18
    min_p_spec: float = 0.90        # skip pairs whose true quality may fail the spec (SPEC_MISMATCH)
    absorb: float = 0.60            # share of offered bags we expect to actually sell per round
    patience: float = 0.6           # 0 = sell now at any k > floor, 1 = full hold value (0.5-0.7 best in sim)
    probes: bool = True             # add 1-bag probe offers while a buyer's ceiling is still uncertain
    probe_rounds: int = 2           # ... only in the first N rounds (info is worth most early)
    probe_min_width: float = 0.04   # ... only when hi - lo is wider than this
    max_share_of_rfq: float = 1.0   # offer at most this share of an RFQ's bags (1.0 = all of it)
    # ---- manual controls (live play): all optional, defaults = fully automatic
    sell_round: bool = True         # False = "ไม่ขายรอบนี้", hold everything
    risk: str = "balanced"          # safe | balanced | aggressive
    bags_min: int | None = None     # sell at least / at most this many bags in total this round
    bags_max: int | None = None
    target_pr: float | None = None  # e.g. 0.95 -> show (and optionally enforce) the k the rest must fetch
    target_enforce: bool = False
    lot_rules: dict = field(default_factory=dict)   # {lot_id: {"mode": auto|hold|exact|max, "bags": n, "min_k": k}}
    rfq_rules: dict = field(default_factory=dict)   # {rfq_id: {"skip", "k", "price", "max_bags", "comp_k"}}
    ladder: bool = False            # split: 1 bag near the ceiling + the rest at k*
    ladder_step: float = 0.02
    undercut: float = 0.0           # random undercut up to this k (e.g. 0.004) so we don't tie with copycats
    seed: int = 0


@dataclass
class Offer:
    rfq_id: str
    lot_id: str
    bags: int
    k: float
    price: float
    p: float
    ev: float               # expected THB uplift vs holding, for all bags
    buyer: str = ""
    buyer_type: str = ""
    kind: str = "main"      # main | probe
    note: str = ""

    def api(self) -> dict:
        return {"rfq_id": self.rfq_id, "lot_id": self.lot_id, "kg": self.bags * BAG, "price_per_kg": self.price}


@dataclass
class Plan:
    offers: list = field(default_factory=list)
    reserve: dict = field(default_factory=dict)       # lot_id -> V used this round
    unsellable: list = field(default_factory=list)    # lots no buyer type can take -> salvage only
    skipped: list = field(default_factory=list)       # (lot, rfq, reason)
    notes: list = field(default_factory=list)
    target: dict = field(default_factory=dict)        # PR-target maths (k_req)
    expected: dict = field(default_factory=dict)      # expected bags / revenue if offers sell with P


# ---------------------------------------------------------------------- helpers

def rfq_bags(rfq: dict) -> int:
    return rfq.get("bags") or rfq["kg"] // BAG


def is_festival(label: str | None) -> bool:
    label = (label or "").lower()
    return any(w in label for w in ("festival", "เทศกาล", "peak", "high"))


def round_labels(calendar: list | None, rounds: int) -> dict[int, str]:
    """calendar from GET /v1/calendar: [{"from":1,"to":2,"label":"..."}] -> {round_number: label}"""
    out = {}
    for row in calendar or []:
        for n in range(int(row.get("from", 0)), int(row.get("to", 0)) + 1):
            out[n] = str(row.get("label", ""))
    return {n: out.get(n, "") for n in range(1, rounds + 1)}


def unsellable_lots(lots: list[dict], known_specs: list[dict], noise=None, min_p=0.9) -> list[str]:
    """Lots whose sample passes none of the buyer specs we know of."""
    return [l["lot_id"] for l in lots
            if not any(mc.pass_probability(l["sample"], s, noise) >= min_p for s in known_specs)]


def sell_budget(lots: list[dict], rounds_left: int, pace: float = 1.3) -> dict[str, int]:
    """Simple even pacing (starter style): kg of each lot to offer this round."""
    out = {}
    for lot in lots:
        avail = lot["kg_available"] // BAG * BAG
        share = avail if rounds_left <= 1 else math.ceil(avail / rounds_left * pace / BAG) * BAG
        out[lot["lot_id"]] = min(avail, max(BAG, share)) if avail >= BAG else 0
    return out


def hold_value(learner: CeilingLearner, btypes: list[str], festival: bool) -> float:
    """Best one-shot value of a bag offered to a buyer type next time: max_k k.P + (1-P).0.80."""
    best = SALVAGE
    for t in btypes:
        b = learner.belief("", t, festival)
        r = best_k(b, reserve=SALVAGE, floor=0.85)
        best = max(best, SALVAGE + r["ev"])
    return best


def reservation_k(stock_bags: int, sell_now_bags: int, future_rounds: list[dict], cfg: PlanConfig) -> float:
    """V for the marginal bag: how much a bag kept for later is worth, in k units.

    future_rounds: [{"value": hold value of that round, "slots": bags we could sell then}]
    Fill the best future slots with the stock left after this round; the marginal bag gets the value of
    the slot it lands in (0.80 if there are not enough future slots -> it will be salvaged).
    """
    left = max(0, stock_bags - sell_now_bags)
    if left == 0 or not future_rounds:
        return SALVAGE
    slots = sorted(((r["value"], r["slots"] * cfg.absorb) for r in future_rounds), reverse=True)
    acc = 0.0
    marginal = SALVAGE
    for value, n in slots:
        acc += n
        marginal = value
        if acc >= left:
            break
    else:
        marginal = SALVAGE              # more stock than future rounds can take
    return SALVAGE + (marginal - SALVAGE) * cfg.patience


# ---------------------------------------------------------------------- main entry

RISK = {   # how sure a k must be (min P(sell)) and how patient we are, per risk profile
    "safe": {"min_p": 0.85, "patience": 0.5},
    "balanced": {"min_p": 0.0, "patience": None},          # None = use cfg.patience
    "aggressive": {"min_p": 0.0, "patience": 0.9},
}


def target_k(target_pr: float, progress: dict, lots: list[dict], tv: dict, unsellable: list) -> dict:
    """Average k the remaining sellable bags must fetch to finish at target_pr.

    progress = {"revenue": THB already earned, "total_value": sum TV x kg_total of ALL lots}.
    final PR = (revenue + sum k.TV.kg (sellable) + 0.80 . TV.kg (unsellable)) / total_value
    """
    sell = sum(tv[l["lot_id"]] * l["kg_available"] for l in lots if l["lot_id"] not in unsellable)
    salv = sum(tv[l["lot_id"]] * l["kg_available"] for l in lots if l["lot_id"] in unsellable) * SALVAGE
    need = target_pr * progress["total_value"] - progress.get("revenue", 0.0) - salv
    if sell <= 0:
        return {"k_req": None, "reachable": need <= 0}
    return {"k_req": need / sell, "sellable_value": sell}


def plan_round(lots: list[dict], rfqs: list[dict], tv: dict, learner: CeilingLearner, *,
               round_number: int, rounds_total: int, capacity_left_kg: int | None = None,
               labels: dict[int, str] | None = None, cfg: PlanConfig | None = None,
               known_types: dict[str, dict] | None = None, progress: dict | None = None) -> Plan:
    """Plan this round's offers.

    lots: [{"lot_id", "kg_available", "sample"}]   rfqs: this round's RFQs from /v1/state
    tv: lot_id -> TV estimate.  labels: {round_number: calendar label}.
    known_types: buyer_type -> spec seen so far (to value future rounds); defaults to this round's RFQs.
    progress: {"revenue", "total_value"} - needed only for cfg.target_pr.

    Manual-control knobs in cfg (all optional): sell_round, risk, bags_min/bags_max, target_pr,
    lot_rules {lot_id: {mode: auto|hold|exact|max, bags, min_k}}, rfq_rules {rfq_id: {skip, k, price,
    max_bags, comp_k}}, ladder, undercut.  With no knobs set it behaves exactly like the automatic planner.
    """
    cfg = cfg or PlanConfig()
    labels = labels or {}
    plan = Plan()
    lots = [l for l in lots if l["kg_available"] >= BAG]
    cap_bags = capacity_left_kg // BAG if capacity_left_kg is not None else None
    risk = RISK.get(cfg.risk, RISK["balanced"])
    patient_cfg = cfg if risk["patience"] is None else PlanConfig(**{**cfg.__dict__, "patience": risk["patience"]})
    lot_rules, rfq_rules = cfg.lot_rules or {}, cfg.rfq_rules or {}

    types = dict(known_types or {})
    for r in rfqs:
        types.setdefault(norm_type(r["buyer_type"]), r["spec"])
    plan.unsellable = unsellable_lots(lots, list(types.values()), cfg.noise, cfg.min_p_spec)

    # ---- PR target -> the average k the rest of the stock must fetch
    floor_all = cfg.floor
    if cfg.target_pr and progress and progress.get("total_value"):
        t = target_k(cfg.target_pr, progress, lots, tv, plan.unsellable)
        plan.target = t
        if t.get("k_req") is not None:
            plan.notes.append(f"PR target {cfg.target_pr:.3f} needs avg k {t['k_req']:.3f} on the rest")
            if cfg.target_enforce and t["k_req"] > floor_all:
                floor_all = min(t["k_req"], cfg.cap)

    if not cfg.sell_round:
        plan.notes.append("hold everything this round (ไม่ขายรอบนี้)")
        return plan

    # ---- hold value V per lot
    stock = sum(l["kg_available"] for l in lots) // BAG
    now_slots = min(stock, cap_bags if cap_bags is not None else stock)
    future = []
    for n in range(round_number + 1, rounds_total + 1):
        per_round_cap = cap_bags if cap_bags is not None else stock
        future.append({"n": n, "festival": is_festival(labels.get(n)), "slots": per_round_cap})
    for lot in lots:
        ok_types = [t for t, spec in types.items()
                    if mc.pass_probability(lot["sample"], spec, cfg.noise) >= cfg.min_p_spec]
        fr = [{"value": hold_value(learner, ok_types, f["festival"]), "slots": f["slots"]} for f in future]
        plan.reserve[lot["lot_id"]] = round(reservation_k(stock, int(now_slots * cfg.absorb), fr, patient_cfg), 4)

    # ---- score every (lot, rfq) pair
    festival_now = is_festival(labels.get(round_number))
    cands, pool = [], []
    for r in rfqs:
        rr = rfq_rules.get(r["rfq_id"], {})
        if rr.get("skip"):
            plan.skipped.append(("*", r["rfq_id"], "skipped by you"))
            continue
        belief = learner.belief(r["buyer"], r["buyer_type"], festival_now)
        if rr.get("comp_k"):                          # you saw / estimated the competitors' level
            belief.clear_k, belief.comp_weight, belief.width = float(rr["comp_k"]), 1.0, 0.01
        for lot in lots:
            lr = lot_rules.get(lot["lot_id"], {})
            if lr.get("mode") == "hold":
                continue
            p_spec = mc.pass_probability(lot["sample"], r["spec"], cfg.noise)
            if p_spec < cfg.min_p_spec:
                if p_spec > 0:
                    plan.skipped.append((lot["lot_id"], r["rfq_id"], f"spec risk p={p_spec:.2f}"))
                continue
            t = tv[lot["lot_id"]]
            floor = max(floor_all, float(lr.get("min_k") or 0))
            V = max(plan.reserve[lot["lot_id"]], floor if (cfg.target_enforce or lr.get("min_k")) else 0)
            forced = rr.get("k") or (float(rr["price"]) / t if rr.get("price") else None)
            if forced:                                # your price for this RFQ wins over the model
                k = float(forced)
                best = {"k": k, "p": belief.p_accept(k) * p_spec}
                best["ev"] = (k - V) * best["p"]
                fallback = best
            else:
                best = best_k(belief, reserve=V, floor=floor, cap=cfg.cap, p_spec=p_spec, min_p=risk["min_p"])
                fallback = best_k(belief, reserve=SALVAGE, floor=floor, cap=cfg.cap, p_spec=p_spec)
            c = {"rfq": r, "lot": lot, "belief": belief, "k": best["k"], "p": best["p"], "ev_bag": best["ev"] * t * BAG,
                 "tv": t, "V": V, "p_spec": p_spec, "forced": bool(forced), "fallback": fallback}
            pool.append(c)
            if best["ev"] <= 0 and not forced:
                plan.skipped.append((lot["lot_id"], r["rfq_id"], f"k*={best['k']:.3f} not above hold value {V:.3f}"))
                continue
            cands.append(c)

    lot_left = {l["lot_id"]: l["kg_available"] // BAG for l in lots}
    for lid, lr in lot_rules.items():                 # exact / max N bags of a lot this round
        if lid in lot_left and lr.get("mode") in ("exact", "max") and lr.get("bags") not in (None, ""):
            lot_left[lid] = min(lot_left[lid], int(lr["bags"]))
    rfq_left = {}
    for r in rfqs:
        n = max(1, int(rfq_bags(r) * cfg.max_share_of_rfq))
        mb = rfq_rules.get(r["rfq_id"], {}).get("max_bags")
        rfq_left[r["rfq_id"]] = min(n, int(mb)) if mb not in (None, "") else n
    room = cap_bags if cap_bags is not None else 10**9
    if cfg.bags_max is not None:
        room = min(room, int(cfg.bags_max))
    rng = random.Random(f"{cfg.seed}:{round_number}")

    def add(c, bags, k, kind, note=""):
        nonlocal room
        if not c["forced"] and cfg.undercut and kind != "probe":   # tiny random undercut: don't tie with copycats
            k = k - rng.uniform(0, cfg.undercut)
        price = mc.price_for(k, c["tv"])
        if price <= SALVAGE * c["tv"]:                # hard rule: never at or below 0.80 x TV
            plan.skipped.append((c["lot"]["lot_id"], c["rfq"]["rfq_id"], "price would be <= 0.80 x TV"))
            return
        p = c["belief"].p_accept(price / c["tv"]) * c["p_spec"]
        plan.offers.append(Offer(c["rfq"]["rfq_id"], c["lot"]["lot_id"], bags, round(price / c["tv"], 4), price, p,
                                 (price / c["tv"] - c["V"]) * c["tv"] * BAG * bags * p,
                                 c["rfq"]["buyer"], norm_type(c["rfq"]["buyer_type"]), kind, note))
        lot_left[c["lot"]["lot_id"]] -= bags
        rfq_left[c["rfq"]["rfq_id"]] -= bags
        room -= bags

    def note_of(c):
        if c["forced"]:
            return "your price"
        b = c["belief"]
        return f"{b.source} ceiling {b.lo:.3f}-{b.hi:.3f}" + (
            f", clear~{b.clear_k:.3f} ({b.comp_weight:.0%} busy)" if b.clear_k else "")

    def place(c, bags):
        """Main offer, optionally as a ladder: 1 bag near the ceiling + the rest at k*."""
        b = c["belief"]
        edge = min(b.hi - 0.005, c["k"] + cfg.ladder_step)
        if cfg.ladder and bags >= 2 and not c["forced"] and edge > c["k"] + 0.004:
            add(c, 1, edge, "edge", f"1 bag near the ceiling ({b.hi:.3f})")
            bags -= 1
        add(c, bags, c["k"], "main", note_of(c))

    def offered(lot_id=None):
        return sum(o.bags for o in plan.offers if lot_id is None or o.lot_id == lot_id)

    want = {lid: int(lr["bags"]) for lid, lr in lot_rules.items()
            if lr.get("mode") == "exact" and lr.get("bags") not in (None, "")}

    def reserved(lot_id):
        """Room kept free for OTHER lots that you asked to sell exactly N bags of."""
        return sum(max(0, n - offered(lid)) for lid, n in want.items() if lid != lot_id)

    # ---- probes first (1 bag each, not in the last round): learn the ceiling while it still pays off
    if cfg.probes and round_number <= cfg.probe_rounds and round_number < rounds_total:
        probed = set()
        for c in sorted(cands, key=lambda c: -rfq_bags(c["rfq"])):
            b, key = c["belief"], norm_type(c["rfq"]["buyer_type"])
            if c["forced"] or key in probed or b.hi - b.lo < cfg.probe_min_width or room - reserved(c["lot"]["lot_id"]) < 2:
                continue
            if lot_left[c["lot"]["lot_id"]] < 2 or rfq_left[c["rfq"]["rfq_id"]] < 2:
                continue
            kp = probe_k(b, cfg.floor)
            if kp <= c["k"] + 0.01:
                continue
            add(c, 1, kp, "probe", f"probe ceiling range {b.lo:.3f}-{b.hi:.3f}")
            probed.add(key)

    # ---- greedy main allocation by expected uplift per bag (ties: higher TV first); your prices first
    for c in sorted(cands, key=lambda c: (c["lot"]["lot_id"] not in want, not c["forced"], -c["ev_bag"], -c["tv"])):
        bags = min(lot_left[c["lot"]["lot_id"]], rfq_left[c["rfq"]["rfq_id"]], room - reserved(c["lot"]["lot_id"]))
        if bags <= 0:
            continue
        place(c, bags)
        if room <= 0:
            break

    # ---- minimums: "exact N" lots and the round's bags_min, filled at the sell-now k (V = 0.80)
    for c in sorted(pool, key=lambda c: (-c["fallback"]["ev"] * c["tv"], -c["tv"])):
        lid = c["lot"]["lot_id"]
        need = max(want.get(lid, 0) - offered(lid), (cfg.bags_min or 0) - offered())
        if need <= 0 or c["fallback"]["ev"] <= 0:
            continue
        bags = min(need, lot_left[lid], rfq_left[c["rfq"]["rfq_id"]], room)
        if bags > 0:
            add({**c, "k": c["fallback"]["k"], "V": SALVAGE}, bags, c["fallback"]["k"], "fill",
                "filled to your minimum at the sell-now k")
    for lid, n in want.items():
        if offered(lid) < n:
            plan.notes.append(f"{lid}: could place only {offered(lid)} of {n} bags (RFQ sizes / capacity / spec)")
    if cfg.bags_min and offered() < cfg.bags_min:
        plan.notes.append(f"could place only {offered()} of the {cfg.bags_min} bags you asked for")

    # merge duplicates (same lot, rfq, price) and drop empty
    merged = {}
    for o in plan.offers:
        key = (o.lot_id, o.rfq_id, o.price)
        if key in merged:
            merged[key].bags += o.bags
            merged[key].ev += o.ev
        else:
            merged[key] = o
    plan.offers = [o for o in merged.values() if o.bags > 0]
    sold_exp = sum(o.bags * o.p for o in plan.offers)
    plan.expected = {"bags": sold_exp, "revenue": sum(o.bags * BAG * o.price * o.p for o in plan.offers)}
    plan.notes.append(f"stock {stock} bags, offering {sum(o.bags for o in plan.offers)}, "
                      f"expected to sell ~{sold_exp:.1f}, expected uplift {sum(o.ev for o in plan.offers):,.0f} THB")
    return plan


def plan_table(plan: Plan) -> str:
    """Plain-text table for a human to type into /trade."""
    head = f"{'RFQ':<8} {'buyer':<18} {'type':<5} {'lot':<10} {'bags':>4} {'k':>6} {'price':>8} {'P':>5} {'EV THB':>8}  note"
    lines = [head, "-" * len(head)]
    for o in sorted(plan.offers, key=lambda o: (o.rfq_id, o.price)):
        lines.append(f"{o.rfq_id:<8} {o.buyer[:18]:<18} {o.buyer_type[:4]:<5} {o.lot_id[-10:]:<10} {o.bags:>4} "
                     f"{o.k:>6.3f} {o.price:>8.2f} {o.p:>5.2f} {o.ev:>8,.0f}  {o.kind}: {o.note}")
    return "\n".join(lines)
