"""Monte-Carlo market simulator: test strategies and search for the best k BEFORE the real rounds (stdlib only).

The matching rules are the official ones (spec check on TRUE quality, drop k > secret ceiling, fill cheapest k
first, pay-as-bid, ties bag by bag).  What is NOT known - buyer ceilings, how other teams bid - is a
parameter (MarketConfig), so you can ask "what if competitors are smarter / ceilings lower?".

Each strategy sees a dict shaped like GET /v1/state (round, lots, rfqs, capacity_left_kg, last_results), and
returns offers shaped like POST /v1/offers:batch items, so the same code runs against the real API.

    python tools/sim.py                       # compare built-in strategies on Day 17 settings
    python tools/sim.py --day 18 --games 200
    python tools/sim.py --grid                # search fixed k per buyer type
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
from dataclasses import dataclass, field

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))  # agent modules

import market_core as mc
from planner import PlanConfig, plan_round, round_labels
from pricing import CeilingLearner, norm_type

BAG = mc.BAG_KG

# name, type, max_defect, max_moisture, min_screen, ceiling base (k).  Ceilings are ASSUMPTIONS.
BUYERS_17 = [
    # generic assumed bases (edit to your own measurements): per-RFQ ceiling = base +- jitter
    ("Commercial A", "COMMERCIAL", 7, 12.5, None, 1.07),
    ("Commercial B", "COMMERCIAL", 7, 12.5, None, 1.07),
    ("Instant A", "INSTANT", 15, 14, None, 0.97),
    ("Instant B", "INSTANT", 15, 14, None, 0.97),
]
BUYERS_18 = [
    ("โรงคั่วดอย", "SPECIALTY", 3, 12.2, 16, 1.13),
    ("โรงคั่วต้นน้ำ", "SPECIALTY", 2.5, 12.0, 17, 1.19),
] + BUYERS_17


@dataclass
class MarketConfig:
    teams: int = 20                       # including us
    rounds: int = 6
    buyers: list = field(default_factory=lambda: list(BUYERS_17))
    ceiling_jitter: float = 0.025         # per-RFQ ceiling = base +- jitter
    festival_rounds: tuple = (3, 4)
    festival_bonus: float = 0.04          # ceiling lift in festival rounds
    festival_demand: float = 1.4
    demand: tuple = (0.40, 0.85)          # RFQ bags = teams x U(demand)
    capacity_factor: float | None = None  # Day 18: 1.5 -> per-round cap = ceil(total_bags x 1.5 / rounds)
    noise: dict | None = None             # Day 18: {"defect": .35, "moisture": .3}
    # how other teams bid: k ~ mean +- spread per type (organiser bots: COM .98, INS .91, SPEC 1.06 +- .045)
    rivals: dict = field(default_factory=lambda: {"SPECIALTY": (1.06, .045), "COMMERCIAL": (.98, .045),
                                                   "INSTANT": (.91, .045)})
    rival_pace: float = 1.5
    # who the other sellers are: share of each style (see Rival). "bot" = organiser bots;
    # naive / safe / edge / undercut = human teams (from research/sim_humans.py)
    rival_mix: dict = field(default_factory=lambda: {"bot": 1.0})
    rfqs_per_buyer: int = 1                # the 17 Nov practice had 2 RFQs per buyer per round
    demand_scale: float = 1.0              # x total demand (demand / supply knob)


# human-competitor scenarios (research/FINDINGS.md: worst case = cheap naive sellers + oversupply)
SCENARIOS = {
    "bots": {"bot": 1.0},
    "naive": {"naive": 0.8, "safe": 0.2},                  # most teams do not know the ceilings
    "learned": {"naive": 0.4, "safe": 0.4, "edge": 0.2},   # half the field priced safely
    "edge": {"edge": 0.7, "safe": 0.3},                    # everyone sits near the ceiling
    "undercut": {"undercut": 0.7, "safe": 0.3},            # price war
    "mixed": {"naive": 0.25, "safe": 0.25, "edge": 0.25, "undercut": 0.25},
}


def day_config(day: int, **kw) -> MarketConfig:
    if day == 18:
        base = dict(teams=56, rounds=8, buyers=list(BUYERS_18), capacity_factor=1.5,
                    noise={"defect": .35, "moisture": .3})
    else:
        base = {}
    base.update(kw)
    return MarketConfig(**base)


# ---------------------------------------------------------------------- lots & RFQs

def make_lots(rng: random.Random, noise: dict | None):
    """Four lots like the organiser's generator: premium, grade-1, grade-2/3, below-standard."""
    entries = [
        (rng.randint(4, 7), rng.uniform(.55, 1.8), rng.uniform(10.8, 12), rng.choice([16, 17, 18])),
        (rng.randint(4, 8), rng.uniform(2.2, 4), rng.uniform(11.5, 12.7), rng.choice([14, 15, 16])),
        (rng.randint(5, 9), rng.uniform(4.9, 7.3), rng.uniform(11.7, 13.2), rng.choice([13, 14, 15])),
        (rng.randint(5, 9), rng.uniform(7.5, 13), rng.uniform(12.3, 13.8), rng.choice([12, 13, 14])),
    ]
    out = []
    for bags, d, m, s in entries:
        true = {"defect_pct": round(d, 2), "moisture_pct": round(m, 2), "screen": s}
        sample = dict(true)
        if noise:
            sample["defect_pct"] = round(d + rng.uniform(-noise["defect"], noise["defect"]), 2)
            sample["moisture_pct"] = round(m + rng.uniform(-noise["moisture"], noise["moisture"]), 2)
        out.append((bags, true, sample))
    return out


def make_rfqs(rng, cfg: MarketConfig, number: int):
    fest = number in cfg.festival_rounds
    out = []
    entries = [b for b in cfg.buyers for _ in range(cfg.rfqs_per_buyer)]
    for i, (name, kind, d, m, s, ceil) in enumerate(entries, 1):
        bags = max(1, round(cfg.teams * rng.uniform(*cfg.demand) * (cfg.festival_demand if fest else 1)
                            * cfg.demand_scale / cfg.rfqs_per_buyer))
        out.append({"rfq_id": f"R{number}-{i:02d}", "buyer": name, "buyer_type": kind, "bags": bags, "_base": ceil,
                    "kg": bags * BAG, "spec": {"max_defect_pct": d, "max_moisture_pct": m, "min_screen": s},
                    "_ceiling": ceil + rng.uniform(-cfg.ceiling_jitter, cfg.ceiling_jitter)
                    + (cfg.festival_bonus if fest else 0)})
    return out


# ---------------------------------------------------------------------- strategies

class Strategy:
    name = "base"

    def new_game(self, rules: dict, calendar: list):
        self.rules, self.calendar, self.tv = rules, calendar, {}

    def appraise(self, lots) -> dict:
        self.tv = {l["lot_id"]: mc.tv(l["sample"]) for l in lots}
        return self.tv

    def offers(self, state: dict) -> list[dict]:
        raise NotImplementedError


class FixedK(Strategy):
    """Same k for every RFQ of a buyer type; sells an even share (x pace) per round, everything in the last."""

    def __init__(self, ks: dict, pace: float = 1.3, name: str | None = None):
        self.ks, self.pace = {norm_type(k): v for k, v in ks.items()}, pace
        self.name = name or "fixed " + " ".join(f"{t[:3]}{v:.2f}" for t, v in self.ks.items())

    def offers(self, state):
        rnd = state["round"]
        left_rounds = rnd["of"] - rnd["number"] + 1
        room = state["capacity_left_kg"] // BAG if state["capacity_left_kg"] is not None else 10**9
        rfq_left = {r["rfq_id"]: r["bags"] for r in state["rfqs"]}
        out = []
        # best k first: send each lot to the highest-paying type it qualifies for
        rfqs = sorted(state["rfqs"], key=lambda r: -self.ks.get(norm_type(r["buyer_type"]), 0))
        for lot in sorted(state["lots"], key=lambda l: -self.tv[l["lot_id"]]):
            bags_all = lot["kg_available"] // BAG
            budget = bags_all if left_rounds <= 1 else min(bags_all, max(1, math.ceil(bags_all / left_rounds * self.pace)))
            for r in rfqs:
                k = self.ks.get(norm_type(r["buyer_type"]))
                if k is None or not mc.passes(lot["sample"], r["spec"]):
                    continue
                n = min(budget, rfq_left[r["rfq_id"]], room)
                if n <= 0:
                    continue
                out.append({"rfq_id": r["rfq_id"], "lot_id": lot["lot_id"], "kg": n * BAG,
                            "price_per_kg": mc.price_for(k, self.tv[lot["lot_id"]])})
                budget -= n
                rfq_left[r["rfq_id"]] -= n
                room -= n
        return out


class Rival(Strategy):
    """Other sellers. style:
       bot      organiser bot: k = type mean +- spread
       naive    human who does not know the ceilings: wide random k (some very cheap)
       safe     knows the buyer base, prices ~2.5 % under it
       edge     knows the base, prices right at it
       undercut starts just under the base and drops 0.01 after every round it did not sell to that buyer
    """

    def __init__(self, cfg: MarketConfig, rng: random.Random, style: str = "bot"):
        self.cfg, self.rng, self.name, self.style = cfg, rng, "rival", style
        self.mem, self.sold_to = {}, set()
        self.bases = {b[0]: b[5] for b in cfg.buyers}

    def k_for(self, q):
        base = self.bases.get(q["buyer"], 1.0)
        t = q["buyer_type"]
        if self.style == "naive":
            lo, hi = {"COMMERCIAL": (0.95, 1.20), "INSTANT": (0.88, 1.10)}.get(t, (1.00, 1.30))
            return self.rng.uniform(lo, hi)
        if self.style == "safe":
            return base * 0.975
        if self.style == "edge":
            return base * self.rng.uniform(0.985, 1.0)
        if self.style == "undercut":
            return max(0.82, self.mem.setdefault(q["buyer"], base * 0.99))
        mean, spread = self.cfg.rivals.get(t, (.95, .045))
        return mean + self.rng.uniform(-spread, spread)

    def learn(self, last):
        if self.style != "undercut" or not last:
            return
        names = {s["rfq_id"]: s["buyer"] for s in last["market_summary"]}
        tried = {names.get(o["rfq_id"]) for o in last["my_offers"]}
        sold = {names.get(o["rfq_id"]) for o in last["my_offers"] if o["filled_kg"]}
        for b in tried - sold:
            if b in self.mem:
                self.mem[b] -= 0.01

    def offers(self, state):
        self.learn(state.get("last_results"))
        rnd = state["round"]
        rounds = rnd["of"] - rnd["number"] + 1
        room = state["capacity_left_kg"] if state["capacity_left_kg"] is not None else 10**9
        demand = {q["rfq_id"]: q["kg"] for q in state["rfqs"]}
        rfqs = sorted(state["rfqs"], key=lambda q: self.rng.random() ** (1 / q["bags"]), reverse=True)
        if self.style != "bot":            # humans go to the best-paying buyer type first
            rfqs.sort(key=lambda q: -self.bases.get(q["buyer"], 1.0) + self.rng.random() * 0.05)
        plan = []
        for lot in sorted(state["lots"], key=lambda l: -self.tv[l["lot_id"]]):
            avail = lot["kg_available"]
            pace = min(avail, max(BAG, math.ceil(avail * self.cfg.rival_pace / rounds / BAG) * BAG))
            for q in rfqs:
                if not mc.passes(lot["sample"], q["spec"]):
                    continue
                kg = min(pace, room, demand[q["rfq_id"]])
                if kg:
                    k = self.k_for(q)
                    plan.append({"lot_id": lot["lot_id"], "rfq_id": q["rfq_id"], "kg": kg,
                                 "price_per_kg": mc.price_for(k, self.tv[lot["lot_id"]])})
                    pace -= kg
                    demand[q["rfq_id"]] -= kg
                    room -= kg
                if not pace or not room:
                    break
        return plan


class PlannerStrategy(Strategy):
    """Our toolkit: CeilingLearner + planner.plan_round."""

    def __init__(self, cfg: PlanConfig | None = None, priors: dict | None = None, name: str = "planner"):
        self.cfg, self.priors, self.name = cfg or PlanConfig(), priors, name

    def new_game(self, rules, calendar):
        super().new_game(rules, calendar)
        self.learner = CeilingLearner(self.priors)
        self.types = {}

    def appraise(self, lots):
        noise = self.cfg.noise
        self.tv = {l["lot_id"]: mc.appraisal_estimate(l["sample"], noise=noise) if noise else mc.tv(l["sample"])
                   for l in lots}
        self.samples = {l["lot_id"]: l["sample"] for l in lots}
        return self.tv

    def offers(self, state):
        last = state.get("last_results")
        if last:
            specs = {r["rfq_id"]: r["spec"] for r in self._last_rfqs} if hasattr(self, "_last_rfqs") else None
            self.learner.observe(last, self.tv, self.samples, specs)
        self._last_rfqs = state["rfqs"]
        for r in state["rfqs"]:
            self.types.setdefault(norm_type(r["buyer_type"]), r["spec"])
        rnd = state["round"]
        plan = plan_round(state["lots"], state["rfqs"], self.tv, self.learner, round_number=rnd["number"],
                          rounds_total=rnd["of"], capacity_left_kg=state["capacity_left_kg"],
                          labels=round_labels(self.calendar, rnd["of"]), cfg=self.cfg, known_types=self.types)
        self.last_plan = plan
        return [o.api() for o in plan.offers]


# ---------------------------------------------------------------------- one game

def run_game(seed, ours: Strategy, cfg: MarketConfig, verbose: bool = False) -> dict:
    rng = random.Random(seed)
    templates = make_lots(rng, cfg.noise)
    calendar = [{"from": n, "to": n, "label": "เทศกาล" if n in cfg.festival_rounds else "ปกติ"}
                for n in range(1, cfg.rounds + 1)]
    teams = {"US": ours}
    for i in range(1, cfg.teams):
        style_rng = random.Random(f"{seed}:style:{i}")
        styles, weights = zip(*cfg.rival_mix.items())
        teams[f"B{i:02d}"] = Rival(cfg, random.Random(f"{seed}:{i}"), style_rng.choices(styles, weights)[0])
    lots = {}
    for t, strat in teams.items():
        strat.new_game(mc.DEFAULT_RULES, calendar)
        for j, (bags, true, sample) in enumerate(templates, 1):
            lots[f"{t}-{j}"] = {"lot_id": f"{t}-{j}", "team": t, "kg_total": bags * BAG, "kg_remaining": bags * BAG,
                                "true": true, "sample": sample, "tv": mc.tv(true)}
        strat.appraise([{"lot_id": l["lot_id"], "sample": l["sample"]} for l in lots.values() if l["team"] == t])
    total_bags = sum(b for b, _, _ in templates)
    cap = (math.ceil(total_bags * cfg.capacity_factor / cfg.rounds) * BAG) if cfg.capacity_factor else None
    revenue = {t: 0.0 for t in teams}
    last = {t: None for t in teams}
    log = []
    for number in range(1, cfg.rounds + 1):
        rfqs = make_rfqs(rng, cfg, number)
        public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rfqs]
        book = []
        for t, strat in teams.items():
            mine = [l for l in lots.values() if l["team"] == t]
            state = {"phase": "ROUND_OPEN", "round": {"round_id": f"R{number}", "number": number, "of": cfg.rounds,
                                                      "kind": "real", "calendar": calendar[number - 1]["label"]},
                     "lots": [{"lot_id": l["lot_id"], "kg_available": l["kg_remaining"], "sample": l["sample"],
                               "kg_total": l["kg_total"]} for l in mine],
                     "rfqs": public, "capacity_left_kg": cap, "last_results": last[t]}
            offers = strat.offers(state)
            # server validation (drop invalid offers instead of rejecting the batch)
            left = {l["lot_id"]: l["kg_remaining"] for l in mine}
            room = cap
            for o in offers:
                errs = mc.validate_offer(o, phase="ROUND_OPEN", rfqs=public,
                                         lots=[{"lot_id": k, "kg_available": v} for k, v in left.items()],
                                         capacity_left_kg=room)
                if errs:
                    continue
                left[o["lot_id"]] -= o["kg"]
                if room is not None:
                    room -= o["kg"]
                book.append({**o, "team": t, "filled_kg": 0, "status": "OPEN", "tie": rng.random()})
        summaries = []
        for r in rfqs:
            offers = [o for o in book if o["rfq_id"] == r["rfq_id"]]
            groups = {}
            for o in offers:
                lot = lots[o["lot_id"]]
                k = o["price_per_kg"] / lot["tv"]
                if not mc.passes(lot["true"], r["spec"]):
                    o["status"] = "SPEC_MISMATCH"
                elif k > r["_ceiling"] + 1e-12:
                    o["status"] = "ABOVE_LIMIT"
                else:
                    groups.setdefault(round(k, 12), []).append(o)
            remaining = r["kg"]
            for k in sorted(groups):
                queue = sorted(groups[k], key=lambda o: o["tie"])
                while queue and remaining:
                    o = queue.pop(0)
                    o["filled_kg"] += BAG
                    remaining -= BAG
                    if o["filled_kg"] < o["kg"]:
                        queue.append(o)
                for o in groups[k]:
                    o["status"] = "ACCEPTED" if o["filled_kg"] == o["kg"] else "PARTIAL" if o["filled_kg"] else "OUTBID"
                    lots[o["lot_id"]]["kg_remaining"] -= o["filled_kg"]
                    revenue[o["team"]] += o["filled_kg"] * o["price_per_kg"]
            sold = [o for o in offers if o["filled_kg"]]
            prices = [o["price_per_kg"] for o in sold]
            bought = r["kg"] - remaining
            summaries.append({"rfq_id": r["rfq_id"], "buyer": r["buyer"], "buyer_type": r["buyer_type"],
                              "wanted_kg": r["kg"], "bought_kg": bought, "offers": len(offers),
                              "price_min": min(prices, default=None), "price_max": max(prices, default=None),
                              "price_avg": round(sum(o["price_per_kg"] * o["filled_kg"] for o in sold) / bought, 2)
                              if bought else None, "ceiling": round(r["_ceiling"], 4)})
        for t in teams:
            last[t] = {"round_id": f"R{number}", "market_summary": [{k: v for k, v in s.items() if k != "ceiling"}
                                                                    for s in summaries],
                       "my_offers": [{k: o[k] for k in ("rfq_id", "lot_id", "kg", "price_per_kg", "status", "filled_kg")}
                                     for o in book if o["team"] == t]}
        log.append({"round": number, "summary": summaries, "ours": last["US"]["my_offers"]})
        if verbose:
            for s in summaries:
                print(f"  R{number} {s['buyer_type'][:4]} ceil {s['ceiling']:.3f} bought {s['bought_kg']//BAG}/"
                      f"{s['wanted_kg']//BAG} offers {s['offers']}")
            for o in last["US"]["my_offers"]:
                print(f"     us {o['rfq_id']} {o['lot_id']} {o['kg']//BAG}b @{o['price_per_kg']} {o['status']}")
    prs = {}
    for t in teams:
        mine = [l for l in lots.values() if l["team"] == t]
        prs[t] = mc.pr(revenue[t], mine)
    best = max(prs.values())
    rank = 1 + sum(1 for v in prs.values() if v > prs["US"])
    sold = sum(l["kg_total"] - l["kg_remaining"] for l in lots.values() if l["team"] == "US")
    total = sum(l["kg_total"] for l in lots.values() if l["team"] == "US")
    return {"pr": prs["US"], "best_pr": best, "rival_best": max(v for t, v in prs.items() if t != "US"),
            "rank": rank, "points": mc.pr_points(prs["US"], best), "sold_share": sold / total, "log": log}


def monte_carlo(make_strategy, cfg: MarketConfig, games: int = 100, seed0: int = 1) -> dict:
    rows = [run_game(seed0 + g, make_strategy(), cfg) for g in range(games)]
    pr = [r["pr"] for r in rows]
    return {"games": games, "pr_mean": statistics.mean(pr), "pr_sd": statistics.pstdev(pr),
            "points_mean": statistics.mean(r["points"] for r in rows),
            "rank_mean": statistics.mean(r["rank"] for r in rows),
            "win_rate": sum(1 for r in rows if r["rank"] == 1) / games,
            "sold_share": statistics.mean(r["sold_share"] for r in rows),
            "rival_best_mean": statistics.mean(r["rival_best"] for r in rows)}


def grid_search(cfg: MarketConfig, games: int = 40, types=("COMMERCIAL", "INSTANT"), lo=0.90, hi=1.12, step=0.02,
                fixed_other: dict | None = None):
    """Try fixed k per buyer type on a grid; returns rows sorted by mean PR (best first)."""
    grid = [round(lo + i * step, 3) for i in range(int(round((hi - lo) / step)) + 1)]
    rows = []
    t1, t2 = types[0], types[1] if len(types) > 1 else None
    for a in grid:
        for b in (grid if t2 else [None]):
            ks = dict(fixed_other or {})
            ks[t1] = a
            if t2:
                ks[t2] = b
            res = monte_carlo(lambda: FixedK(ks), cfg, games)
            rows.append({"ks": ks, **res})
    return sorted(rows, key=lambda r: -r["pr_mean"])


def standard_strategies(cfg: MarketConfig) -> dict:
    noise = cfg.noise
    return {
        "starter (k=1.00)": lambda: FixedK({"SPECIALTY": 1.0, "COMMERCIAL": 1.0, "INSTANT": 1.0}, name="starter"),
        "slide (COM1.06/INS0.97)": lambda: FixedK({"SPECIALTY": 1.12, "COMMERCIAL": 1.06, "INSTANT": .97}),
        "planner": lambda: PlannerStrategy(PlanConfig(noise=noise)),
        "planner no-probe": lambda: PlannerStrategy(PlanConfig(noise=noise, probes=False)),
        "planner patient (1.0)": lambda: PlannerStrategy(PlanConfig(noise=noise, patience=1.0)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, default=17)
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--teams", type=int)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--show", type=int, help="print one game (seed) round by round for the planner")
    a = ap.parse_args()
    cfg = day_config(a.day, **({"teams": a.teams} if a.teams else {}))
    if a.show is not None:
        r = run_game(a.show, PlannerStrategy(PlanConfig(noise=cfg.noise)), cfg, verbose=True)
        print(f"PR {r['pr']:.4f} best {r['best_pr']:.4f} rank {r['rank']} points {r['points']:.1f}")
        return
    if a.grid:
        types = ("COMMERCIAL", "INSTANT") if a.day == 17 else ("SPECIALTY", "COMMERCIAL")
        other = None if a.day == 17 else {"INSTANT": 0.95}
        for r in grid_search(cfg, a.games, types, fixed_other=other)[:10]:
            print(f"{r['ks']}  PR {r['pr_mean']:.4f}  points {r['points_mean']:.1f}  rank {r['rank_mean']:.1f}")
        return
    print(f"Day {a.day}: {cfg.teams} teams, {cfg.rounds} rounds, {a.games} games each (ceilings/rivals are assumptions)")
    for name, make in standard_strategies(cfg).items():
        r = monte_carlo(make, cfg, a.games)
        print(f"{name:<26} PR {r['pr_mean']:.4f} +-{r['pr_sd']:.3f}  points {r['points_mean']:5.1f}  "
              f"rank {r['rank_mean']:4.1f}  win {r['win_rate']:.0%}  sold {r['sold_share']:.0%}")


if __name__ == "__main__":
    main()
