"""Strategy endpoints for the local training web: k* finder, round planner, simulator, k grid.

Everything here is offline maths on numbers you type in (or on the LOCAL practice market).
Nothing talks to the real competition market.
"""
from __future__ import annotations

import time
from pathlib import Path

import manual_helper
import market_core as mc
import sim
from planner import PlanConfig, plan_round, plan_table, round_labels
from pricing import PRIORS, CeilingLearner, best_k, ladder, norm_type

MAX_SIM_GAMES = 4000          # games x strategies per request, keeps a request under ~1 minute


def _f(data, key, default=None, low=None, high=None):
    v = data.get(key)
    if v in (None, ""):
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: ต้องเป็นตัวเลข")
    if (low is not None and x < low) or (high is not None and x > high):
        raise ValueError(f"{key}: ต้องอยู่ระหว่าง {low}–{high}")
    return x


def _pct(data, key, default=None):
    """Inputs typed as % (e.g. 106 for k = 1.06)."""
    v = _f(data, key, None, 1, 300)
    return v / 100 if v is not None else default


def _on(data, key, default="0"):
    return str(data.get(key, default)) not in ("0", "false", "False", "", "off")


def _int_or_none(data, key, low=0, high=10_000):
    v = _f(data, key, None, low, high)
    return int(v) if v is not None else None


def plan_config(data: dict) -> PlanConfig:
    """All planner knobs a page can send. Missing keys = automatic defaults."""
    md, mm = _f(data, "margin_defect", 0, 0, 3), _f(data, "margin_moisture", 0, 0, 3)
    # keep P(true lot passes) >= 0.9 with uniform noise n  <=>  margin = 0.8 n   (so n = margin / 0.8)
    noise = {"defect": md / 0.8, "moisture": mm / 0.8} if (md or mm) else None
    lot_rules = {}
    for lid, r in (data.get("lot_rules") or {}).items():
        if not isinstance(r, dict):
            continue
        if r.get("mode", "auto") == "auto" and r.get("min_k") in (None, ""):
            continue
        lot_rules[lid] = {"mode": r.get("mode", "auto"), "bags": _int_or_none(r, "bags", 0, 1000),
                          "min_k": _pct(r, "min_k")}
    rfq_rules = {}
    for rid, r in (data.get("rfq_rules") or {}).items():
        if not isinstance(r, dict):
            continue
        rule = {"skip": _on(r, "skip"), "k": _pct(r, "k"), "price": _f(r, "price", None, 0.01, 1000),
                "max_bags": _int_or_none(r, "max_bags", 0, 1000), "comp_k": _pct(r, "comp_k"),
                "comp_price": _f(r, "comp_price", None, 0.01, 1000)}
        if any(v not in (None, False) for v in rule.values()):
            rfq_rules[rid] = rule
    risk = data.get("risk") if data.get("risk") in ("safe", "balanced", "aggressive") else "balanced"
    return PlanConfig(floor=_pct(data, "floor", 0.85), patience=_f(data, "patience", 0.6, 0, 1),
                      probes=_on(data, "probes", "1"), noise=noise, risk=risk,
                      sell_round=_on(data, "sell_round", "1"),
                      bags_min=_int_or_none(data, "bags_min"), bags_max=_int_or_none(data, "bags_max"),
                      target_pr=_pct(data, "target_pr"), target_enforce=_on(data, "target_enforce"),
                      ladder=_on(data, "ladder"), undercut=_f(data, "undercut", 0, 0, 5) / 100,
                      seed=int(_f(data, "seed", 0, 0, 10**9)), lot_rules=lot_rules, rfq_rules=rfq_rules)


# ---------------------------------------------------------------------- k* finder

def kstar(data: dict) -> dict:
    btype = norm_type(data.get("buyer_type") or "COMMERCIAL")
    learner = CeilingLearner()
    lo, hi = _pct(data, "lo"), _pct(data, "hi")
    if lo is not None or hi is not None:
        p_lo, p_hi = learner.priors.get(btype, (0.9, 1.1))
        learner.priors[btype] = (lo if lo is not None else p_lo, hi if hi is not None else p_hi)
    ev, _ = learner._ev("buyer", btype)
    if _pct(data, "passed") is not None:
        ev.passed.append(_pct(data, "passed"))
    if _pct(data, "cut") is not None:
        ev.above.append(_pct(data, "cut"))
    belief = learner.belief("buyer", btype, festival=str(data.get("festival", "")) in ("1", "true", "on"))
    clear = _pct(data, "clear")
    if clear is not None:
        belief.clear_k = clear
    reserve = _pct(data, "reserve", 0.80)
    floor = _pct(data, "floor", 0.85)
    tv = _f(data, "tv", 100.0, 0.01, 1000)
    bags = int(_f(data, "bags", 1, 1, 1000))
    r = best_k(belief, reserve=reserve, floor=floor, cap=min(1.5, belief.hi + 0.05))
    price = mc.price_for(r["k"], tv)
    rungs = ladder(belief, bags, reserve=reserve, floor=floor)
    step = max(1, len(r["curve"]) // 80)
    return {
        "k": r["k"], "p": r["p"], "ev_per_k": r["ev"], "price": price, "lo": belief.lo, "hi": belief.hi,
        "clear_k": belief.clear_k, "reserve": reserve,
        "ev_thb": (price / tv - reserve) * tv * mc.BAG_KG * bags * r["p"],
        "sure_k": belief.lo, "sure_ev_thb": max(0.0, (belief.lo - reserve)) * tv * mc.BAG_KG * bags * belief.p_compete(belief.lo),
        "curve": [{"k": k, "p": p, "ev": e} for k, p, e in r["curve"][::step]],
        "ladder": [{"bags": b, "k": k, "price": mc.price_for(k, tv)} for b, k in rungs],
        "prior": PRIORS.get(btype),
    }


# ---------------------------------------------------------------------- round planner (text)

def plan_text(data: dict) -> dict:
    text = str(data.get("text", ""))
    if len(text) > 20000:
        raise ValueError("ข้อความยาวเกินไป")
    game = manual_helper.parse_text(text)
    if not game["lots"]:
        raise ValueError("ยังไม่มีบรรทัด LOT")
    res = manual_helper.advise(game, plan_config(data))
    plan = res["plan"]
    return {"report": manual_helper.report(game, res), "errors": game["errors"],
            "offers": [offer_row(o) for o in plan.offers] if plan else [],
            "unsellable": plan.unsellable if plan else [], "pr_now": res["pr_now"]}


def offer_row(o) -> dict:
    return {"rfq_id": o.rfq_id, "buyer": o.buyer, "buyer_type": o.buyer_type, "lot_id": o.lot_id,
            "bags": o.bags, "k": o.k, "price": o.price, "p": o.p, "ev": o.ev, "kind": o.kind, "note": o.note,
            "api": o.api()}


# ---------------------------------------------------------------------- practice-market recommendation

def recommend(state: dict, cfg: PlanConfig) -> dict:
    """Plan for the LOCAL practice market's current round, learning from its history."""
    if state.get("phase") != "ROUND_OPEN":
        raise ValueError("ใช้คำแนะนำได้เมื่อรอบขายเปิดอยู่")
    tv = {l["lot_id"]: float(l["calc"]["tv_raw"]) for l in state["lots"]}
    samples = {l["lot_id"]: l["sample"] for l in state["lots"]}
    learner = CeilingLearner()
    for res in state.get("history", []):
        learner.observe(res, tv, samples)
    rnd = state["round"]
    plan = plan_round([{"lot_id": l["lot_id"], "kg_available": l["kg_available"] +
                        sum(o["kg"] for o in state["my_open_offers"] if o["lot_id"] == l["lot_id"]),
                        "sample": l["sample"]} for l in state["lots"]],
                      state["rfqs"], tv, learner, round_number=rnd["number"], rounds_total=rnd["of"],
                      capacity_left_kg=state.get("round_capacity_kg"),
                      labels={n: ("เทศกาล" if n in (3, 4) else "ปกติ") for n in range(1, rnd["of"] + 1)}, cfg=cfg)
    beliefs = []
    for r in state["rfqs"]:
        b = learner.belief(r["buyer"], r["buyer_type"])
        beliefs.append({"rfq_id": r["rfq_id"], "buyer": r["buyer"], "buyer_type": r["buyer_type"],
                        "lo": b.lo, "hi": b.hi, "clear_k": b.clear_k, "source": b.source})
    return {"offers": [offer_row(o) for o in plan.offers], "reserve": plan.reserve, "notes": plan.notes,
            "unsellable": plan.unsellable, "skipped": [list(s) for s in plan.skipped[:20]], "beliefs": beliefs,
            "table": plan_table(plan)}


# ---------------------------------------------------------------------- simulator

def market_config(data: dict) -> sim.MarketConfig:
    day = int(_f(data, "day", 17, 17, 20))
    kw = {}
    if data.get("teams") not in (None, ""):
        kw["teams"] = int(_f(data, "teams", 20, 2, 80))
    shift = _f(data, "ceiling_shift", 0.0, -30, 30) / 100
    rivals = {}
    for t, default in (("COMMERCIAL", .98), ("INSTANT", .91), ("SPECIALTY", 1.06)):
        mean = _pct(data, f"rival_{t[:3].lower()}", default)
        spread = _f(data, "rival_spread", 4.5, 0, 30) / 100
        rivals[t] = (mean, spread)
    kw["rivals"] = rivals
    scenario = data.get("scenario") or "bots"
    if scenario in sim.SCENARIOS:
        kw["rival_mix"] = sim.SCENARIOS[scenario]
    kw["demand_scale"] = _f(data, "demand_scale", 1.0, 0.2, 3)
    kw["rfqs_per_buyer"] = int(_f(data, "rfqs_per_buyer", 1, 1, 4))
    cfg = sim.day_config(day, **kw)
    if shift:
        cfg.buyers = [(n, t, d, m, s, c + shift) for n, t, d, m, s, c in cfg.buyers]
    return cfg


def simulate(data: dict) -> dict:
    cfg = market_config(data)
    games = int(_f(data, "games", 40, 1, 1000))
    strategies = sim.standard_strategies(cfg)
    fixed = {"COMMERCIAL": _pct(data, "fixed_com"), "INSTANT": _pct(data, "fixed_ins"),
             "SPECIALTY": _pct(data, "fixed_spe")}
    fixed = {k: v for k, v in fixed.items() if v is not None}
    if fixed:
        strategies["your fixed k " + " ".join(f"{k[:3]} {v:.2f}" for k, v in fixed.items())] = lambda: sim.FixedK(fixed)
    pc = plan_config(data)
    pc.noise = cfg.noise
    strategies["planner (your settings)"] = lambda: sim.PlannerStrategy(PlanConfig(**pc.__dict__))
    if games * len(strategies) * max(1, cfg.teams // 20) > MAX_SIM_GAMES:
        raise ValueError(f"ลดจำนวนเกม: สูงสุดราว {MAX_SIM_GAMES // len(strategies) // max(1, cfg.teams // 20)} เกม")
    t0 = time.time()
    rows = []
    for name, make in strategies.items():
        r = sim.monte_carlo(make, cfg, games)
        rows.append({"name": name, **r})
    rows.sort(key=lambda r: -r["points_mean"])
    sample = sim.run_game(1, sim.PlannerStrategy(PlanConfig(**pc.__dict__)), cfg)
    return {"rows": rows, "seconds": round(time.time() - t0, 1), "teams": cfg.teams, "rounds": cfg.rounds,
            "sample_log": [{"round": rnd["round"],
                            "rfqs": [{k: s[k] for k in ("rfq_id", "buyer_type", "wanted_kg", "bought_kg", "offers",
                                                        "ceiling")} for s in rnd["summary"]],
                            "ours": rnd["ours"]} for rnd in sample["log"]],
            "sample_pr": sample["pr"], "sample_best": sample["best_pr"]}


def grid(data: dict) -> dict:
    cfg = market_config(data)
    games = int(_f(data, "games", 8, 1, 100))
    day = int(_f(data, "day", 17))
    t1, t2 = ("COMMERCIAL", "INSTANT") if day == 17 else ("SPECIALTY", "COMMERCIAL")
    other = {} if day == 17 else {"INSTANT": _pct(data, "fixed_ins", 0.95)}
    a_vals = [round(0.94 + 0.02 * i, 2) for i in range(10)] if t1 == "COMMERCIAL" else [round(1.02 + 0.02 * i, 2) for i in range(10)]
    b_vals = [round(0.86 + 0.02 * i, 2) for i in range(8)] if t2 == "INSTANT" else [round(0.94 + 0.02 * i, 2) for i in range(10)]
    if games * len(a_vals) * len(b_vals) * max(1, cfg.teams // 20) > MAX_SIM_GAMES:
        raise ValueError("ลดจำนวนเกมต่อช่อง")
    cells = []
    for a in a_vals:
        row = []
        for b in b_vals:
            r = sim.monte_carlo(lambda: sim.FixedK({**other, t1: a, t2: b}), cfg, games)
            row.append({"pr": r["pr_mean"], "points": r["points_mean"]})
        cells.append(row)
    best = max(((a, b, cells[i][j]) for i, a in enumerate(a_vals) for j, b in enumerate(b_vals)),
               key=lambda x: x[2]["pr"])
    return {"types": [t1, t2], "a": a_vals, "b": b_vals, "cells": cells,
            "best": {"a": best[0], "b": best[1], **best[2]}}


# ---------------------------------------------------------------------- multi-lot sheet (calculator)

SHEET_TYPES = ("SPECIALTY", "COMMERCIAL", "INSTANT")


def _evidence_learner(data: dict) -> CeilingLearner:
    """Priors + optional evidence typed per buyer type: evidence = {"COMMERCIAL": {"passed": 106, "cut": 125}}."""
    learner = CeilingLearner()
    for btype, ev in (data.get("evidence") or {}).items():
        t = norm_type(btype)
        # typed evidence is the user's own observation -> buyer-level (widened by 1x jitter, not 2x)
        en, _ = learner._ev(f"__{t}", t)
        for key, target in (("passed", en.passed), ("cut", en.above)):
            v = _pct(ev, key)
            if v is not None:
                target.append(v)
        lo, hi = _pct(ev, "lo"), _pct(ev, "hi")
        if lo is not None or hi is not None:
            p_lo, p_hi = learner.priors.get(t, (0.9, 1.1))
            learner.priors[t] = (lo if lo is not None else p_lo, hi if hi is not None else p_hi)
    return learner


def sheet(data: dict) -> dict:
    """Value many lots at once and build the 'max PR' table.

    lots: [{lot_id, moisture, defect, screen, bags}].  For every lot x buyer type it can pass:
    k* = argmax (k - V) x P(k) with V = reserve (0.80 = no later rounds), price to type, expected uplift.
    """
    rows_in = data.get("lots") or []
    if not isinstance(rows_in, list) or not 1 <= len(rows_in) <= 12:
        raise ValueError("ใส่ล็อต 1–12 แถว")
    reserve = _pct(data, "reserve", 0.80)
    floor = _pct(data, "floor", 0.85)
    festival = str(data.get("festival", "")) in ("1", "true", "on")
    learner = _evidence_learner(data)
    specs = {t: dict(manual_helper.DEFAULT_SPECS[t]) for t in SHEET_TYPES}
    for t, sp in (data.get("specs") or {}).items():
        t = norm_type(t)
        if t in specs and isinstance(sp, dict):
            specs[t] = {"max_defect_pct": _f(sp, "max_defect_pct", specs[t]["max_defect_pct"]),
                        "max_moisture_pct": _f(sp, "max_moisture_pct", specs[t]["max_moisture_pct"]),
                        "min_screen": int(_f(sp, "min_screen")) if sp.get("min_screen") not in (None, "", "-") else None}
    use_spec = str(data.get("specialty", "0")) in ("1", "true", "on")
    lots, total_tv_kg = [], 0.0
    for i, r in enumerate(rows_in):
        try:
            sample = {"moisture_pct": _f(r, "moisture", None, 0, 100), "defect_pct": _f(r, "defect", None, 0, 100),
                      "screen": int(_f(r, "screen", None, 1, 99))}
            bags = int(_f(r, "bags", 1, 1, 1000))
        except (TypeError, ValueError):
            sample = None
        if sample is None or None in sample.values():
            raise ValueError(f"แถว {i + 1}: กรอกความชื้น เมล็ดเสีย และตะแกรงให้ครบ")
        b = mc.true_value(sample)
        lots.append({"lot_id": str(r.get("lot_id") or f"ล็อต {i + 1}"), "sample": sample, "bags": bags, **b})
        total_tv_kg += b["tv"] * bags * mc.BAG_KG
    best_rows, exp_rev, exp_salv, max_rev = [], 0.0, 0.0, 0.0
    for lot in lots:
        options = []
        for t in SHEET_TYPES:
            if t == "SPECIALTY" and not use_spec:
                continue
            if not mc.passes(lot["sample"], specs[t]):
                options.append({"buyer_type": t, "eligible": False})
                continue
            belief = learner.belief(f"__{t}", t, festival)
            r = best_k(belief, reserve=reserve, floor=floor)
            price = mc.price_for(r["k"], lot["tv"])
            k = price / lot["tv"]
            p = belief.p_accept(k)
            kg = lot["bags"] * mc.BAG_KG
            options.append({"buyer_type": t, "eligible": True, "k": k, "price": price, "p": p,
                            "lo": belief.lo, "hi": belief.hi,
                            "uplift": (k - reserve) * lot["tv"] * kg * p,
                            "revenue_if_sold": price * kg,
                            "delta_pr": (k - mc.SALVAGE) * lot["tv"] * kg / total_tv_kg,
                            "max_price": mc.price_for(belief.hi, lot["tv"])})
        ok = [o for o in options if o["eligible"]]
        best = max(ok, key=lambda o: o["uplift"]) if ok else None
        kg = lot["bags"] * mc.BAG_KG
        if best:
            exp_rev += best["p"] * best["price"] * kg
            exp_salv += (1 - best["p"]) * mc.SALVAGE * lot["tv"] * kg
            max_rev += best["price"] * kg
        else:
            exp_salv += mc.SALVAGE * lot["tv"] * kg
            max_rev += mc.SALVAGE * lot["tv"] * kg
        best_rows.append({"lot_id": lot["lot_id"], "best": best, "options": options})
        lot["value"] = lot["tv"] * kg
        lot["salvage_price"] = mc.min_price(lot["tv"])
    pr_expected = (exp_rev + exp_salv) / total_tv_kg
    pr_all_sold = max_rev / total_tv_kg
    leader = _pct(data, "leader_pr")
    return {
        "lots": [{k: lot[k] for k in ("lot_id", "bags", "grade", "G", "M", "S", "tv", "tv_2dp", "value", "salvage_price")}
                 | {"salvage_80": mc.SALVAGE * lot["tv"]} for lot in lots],
        "plan": best_rows, "total_value": total_tv_kg,
        "pr_expected": pr_expected, "pr_all_sold": pr_all_sold,
        "points_expected": mc.pr_points(pr_expected, leader) if leader else None,
        "appraisal": [{"lot_id": lot["lot_id"], "est_value_per_kg": lot["tv_2dp"]} for lot in lots],
    }


# ---------------------------------------------------------------------- Round 1 (manual) workspace

def round1(data: dict) -> dict:
    game = manual_helper.game_from_dict(data.get("game") or {})
    if not game["lots"]:
        raise ValueError("ยังไม่มีล็อต กรอกในตารางล็อตก่อน")
    cfg = plan_config(data)
    cap_bags = _int_or_none(data, "cap_bags", 1, 1000)
    if cap_bags:                                           # Day 18+: capacity_left_kg per round
        for rnd in game["rounds"]:
            rnd["cap"] = rnd["cap"] if rnd["cap"] is not None else cap_bags * mc.BAG_KG
    # competitor price (THB) -> k, using the mean TV of OUR lots that pass that RFQ (other lots are similar)
    cur_round = game["rounds"][-1] if game["rounds"] else None
    for rid, rule in cfg.rfq_rules.items():
        if rule.get("comp_price") and not rule.get("comp_k") and cur_round and rid in cur_round["rfqs"]:
            spec = cur_round["rfqs"][rid]["spec"]
            tvs = [mc.tv(l["sample"]) for l in game["lots"].values() if mc.passes(l["sample"], spec)]
            if tvs:
                rule["comp_k"] = rule["comp_price"] / (sum(tvs) / len(tvs))
    res = manual_helper.advise(game, cfg)
    plan, learner = res["plan"], res["learner"]
    beliefs = []
    seen = set()
    for rnd in game["rounds"]:
        for q in rnd["rfqs"].values():
            if q["buyer"] in seen:
                continue
            seen.add(q["buyer"])
            b = learner.belief(q["buyer"], q["buyer_type"])
            ev = learner.by_name.get(q["buyer"])
            beliefs.append({"buyer": q["buyer"], "buyer_type": q["buyer_type"], "lo": b.lo, "hi": b.hi,
                            "clear_k": b.clear_k, "source": b.source,
                            "passed": max(ev.passed) if ev and ev.passed else None,
                            "cut": min(ev.above) if ev and ev.above else None,
                            "fills": ev.fills if ev else []})
    cur = res["round"]
    eligible = {}
    if cur:
        for q in cur["rfqs"].values():
            eligible[q["rfq_id"]] = [l["lot_id"] for l in res["lots"]
                                     if l["kg_available"] >= mc.BAG_KG and mc.passes(l["sample"], q["spec"])]
    return {
        "errors": game["errors"], "pr_now": res["pr_now"], "revenue": res["revenue"],
        "stock": [{"lot_id": l["lot_id"], "left": l["kg_available"] // mc.BAG_KG, "total": l["kg_total"] // mc.BAG_KG,
                   "tv": res["tv"][l["lot_id"]]} for l in res["lots"]],
        "appraisal": [{"lot_id": k, **{x: v for x, v in mc.true_value(l["sample"]).items() if x != "step"}}
                      for k, l in game["lots"].items()],
        "offers": [offer_row(o) for o in plan.offers] if plan else [],
        "reserve": plan.reserve if plan else {}, "unsellable": plan.unsellable if plan else [],
        "skipped": [list(s) for s in plan.skipped[:20]] if plan else [], "notes": plan.notes if plan else [],
        "beliefs": beliefs, "eligible": eligible, "round": cur["number"] if cur else None,
        "target": plan.target if plan else {}, "expected": _expected_after(res, plan),
        "comp_k": {rid: r.get("comp_k") for rid, r in cfg.rfq_rules.items() if r.get("comp_k")},
        "intel": _competition_intel(game, res["tv"]),
        "planned": plan is not None, "text": manual_helper.to_text(game),
    }


def _expected_after(res, plan) -> dict:
    """PR if the market closed right after this round, assuming offers sell with their P."""
    tv = res["tv"]
    total = sum(tv[l["lot_id"]] * l["kg_total"] for l in res["lots"])
    left = sum(tv[l["lot_id"]] * l["kg_available"] for l in res["lots"])
    if not total:
        return {}
    offers = plan.offers if plan else []
    exp_rev = sum(o.bags * mc.BAG_KG * o.price * o.p for o in offers)
    exp_tv = sum(o.bags * mc.BAG_KG * tv[o.lot_id] * o.p for o in offers)
    all_rev = sum(o.bags * mc.BAG_KG * o.price for o in offers)
    all_tv = sum(o.bags * mc.BAG_KG * tv[o.lot_id] for o in offers)
    return {"pr_expected": (res["revenue"] + exp_rev + mc.SALVAGE * (left - exp_tv)) / total,
            "pr_if_all_sell": (res["revenue"] + all_rev + mc.SALVAGE * (left - all_tv)) / total,
            "bags_expected": plan.expected.get("bags") if plan else 0}


def _competition_intel(game, tv) -> list[dict]:
    """Per buyer, from the MARKET lines you typed: how crowded, and at what k other sellers cleared.
    k uses the mean TV of our lots that pass the RFQ (other teams have similar, not equal, lots)."""
    rows = {}
    for rnd in game["rounds"]:
        for rid, m in rnd["market"].items():
            q = rnd["rfqs"].get(rid)
            if not q:
                continue
            tvs = [tv[k] for k, l in game["lots"].items() if mc.passes(l["sample"], q["spec"])]
            ref = sum(tvs) / len(tvs) if tvs else None
            r = rows.setdefault(q["buyer"], {"buyer": q["buyer"], "buyer_type": q["buyer_type"], "rfqs": 0,
                                             "offers": [], "fills": [], "k_min": [], "k_max": []})
            r["rfqs"] += 1
            if m.get("offers") is not None:
                r["offers"].append(m["offers"])
            if m.get("wanted_kg"):
                r["fills"].append(m["bought_kg"] / m["wanted_kg"])
            if ref and m.get("price_min"):
                r["k_min"].append(m["price_min"] / ref)
            if ref and m.get("price_max"):
                r["k_max"].append(m["price_max"] / ref)
    out = []
    for r in rows.values():
        out.append({"buyer": r["buyer"], "buyer_type": r["buyer_type"], "rfqs": r["rfqs"],
                    "offers_avg": sum(r["offers"]) / len(r["offers"]) if r["offers"] else None,
                    "fill_avg": sum(r["fills"]) / len(r["fills"]) if r["fills"] else None,
                    "k_min": min(r["k_min"]) if r["k_min"] else None,
                    "k_max_last": r["k_max"][-1] if r["k_max"] else None})
    return out


# ---------------------------------------------------------------------- Round 2-3 (agent) workspace

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "agent"
SECRET_KEYS = {"TEAM_KEY", "LLM_API_KEY"}


def _env_summary() -> dict:
    path = AGENT_DIR / ".env"
    if not path.exists():
        return {"exists": False, "values": {}}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = (x.strip() for x in line.split("=", 1))
            values[k] = ("set (" + str(len(v)) + " chars)" if v and "change-me" not in v else "NOT SET") \
                if k in SECRET_KEYS else v
    return {"exists": True, "values": values}


def agent_status(data: dict) -> dict:
    """Read-only view of the agent's own log + memory files (logs/decisions.jsonl, logs/memory-*.json)."""
    import json
    logs = AGENT_DIR / "logs"
    events = []
    path = logs / "decisions.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines()[-400:]:
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    tv = {}
    for e in events:
        if e.get("event") == "new_game":
            tv = e.get("tv") or tv
    memories = sorted(logs.glob("memory-*.json"), key=lambda p: p.stat().st_mtime) if logs.exists() else []
    rounds, sent, mem_name = [], {}, None
    if memories:
        mem_name = memories[-1].name
        try:
            m = json.loads(memories[-1].read_text(encoding="utf-8"))
            rounds, sent = m.get("rounds", []), m.get("sent", {})
        except ValueError:
            pass
    learner = CeilingLearner()
    for r in rounds:
        learner.observe(r, tv)
    beliefs = []
    for name, ev in learner.by_name.items():
        btype = next((s.get("buyer_type") for r in rounds for s in r.get("market_summary", []) if s.get("buyer") == name), "?")
        b = learner.belief(name, btype)
        beliefs.append({"buyer": name, "buyer_type": btype, "lo": b.lo, "hi": b.hi, "clear_k": b.clear_k,
                        "passed": max(ev.passed) if ev.passed else None, "cut": min(ev.above) if ev.above else None,
                        "fills": ev.fills})
    revenue = sum(o.get("filled_kg", 0) * o.get("price_per_kg", 0) for r in rounds if r.get("kind", "real") != "practice"
                  for o in r.get("my_offers", []))
    out_rounds = []
    for r in rounds:
        out_rounds.append({"round_id": r.get("round_id"), "kind": r.get("kind"),
                           "my_offers": [{**o, "k": (o["price_per_kg"] / tv[o["lot_id"]]) if o.get("lot_id") in tv else None}
                                         for o in r.get("my_offers", []) if o.get("status") != "CANCELLED"],
                           "market_summary": r.get("market_summary", [])})
    sent_events = [{"time": e.get("time"), "round": e.get("round"), "source": e.get("source"), "n": e.get("n"),
                    "kg": e.get("kg"), "seconds": e.get("seconds"), "offers": e.get("offers", []),
                    "notes": (e.get("notes") or [])[:6]}
                   for e in events if e.get("event") == "offers_sent"]
    problems = [e for e in events if e.get("event") in ("batch_rejected", "offer_rejected", "strategy_crashed",
                                                         "state_error", "appraisal_error")][-15:]
    closed = next((e for e in reversed(events) if e.get("event") == "market_closed"), None)
    return {"env": _env_summary(), "tv": tv, "memory": mem_name, "rounds": out_rounds[-10:],
            "sent": sent_events[-10:], "problems": problems, "beliefs": beliefs, "revenue": revenue,
            "closed": closed, "last_event": events[-1] if events else None, "n_events": len(events)}


def preflight(data: dict) -> dict:
    """Play the real strategy.py in the organiser's engine (in-process, no network)."""
    import arena
    preset = "day18" if str(data.get("preset", "day18")) == "day18" else "day17"
    games = int(_f(data, "games", 3, 1, 10))
    t0 = time.time()
    rows = [arena.run(preset, seed=g, n_bots=int(_f(data, "bots", 19, 1, 40))) for g in range(games)]
    return {"rows": rows, "seconds": round(time.time() - t0, 1), "preset": preset}


# ---------------------------------------------------------------------- /research page

def _research_belief(data: dict):
    import research_tools as research
    lo, hi = _pct(data, "lo", 1.02), _pct(data, "hi", 1.12)
    if lo >= hi:
        raise ValueError("เพดานต่ำสุดต้องน้อยกว่าเพดานสูงสุด")
    return research.belief_p(lo, hi, _pct(data, "clear"), _f(data, "width", 2, 0.1, 20) / 100)


def research_tool(data: dict) -> dict:
    import research_tools as research
    kind = data.get("kind")
    reserve = _pct(data, "reserve", 0.80)
    if kind == "bidcurve":
        rows = data.get("rows") or []
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("ใส่ประวัติได้สูงสุด 500 แถว")
        prior = (_pct(data, "lo", 0.95), _pct(data, "hi", 1.10)) if str(data.get("use_prior", "1")) == "1" else None
        r = research.bid_curve(rows, reserve=reserve, floor=_pct(data, "floor", 0.85), prior=prior)
        tv = _f(data, "tv", None, 0.01, 1000)
        r["price"] = mc.price_for(r["best"]["k"], tv) if tv else None
        return r
    if kind == "dp":
        festival = {int(x) for x in str(data.get("festival", "")).replace(",", " ").split() if x.strip().isdigit()}
        return research.inventory_dp(int(_f(data, "bags", 26, 1, 80)), int(_f(data, "rounds", 6, 1, 12)),
                                     int(_f(data, "per_round", 8, 1, 80)), _research_belief(data),
                                     festival=festival, festival_shift=_pct(data, "festival_shift", 0.0) or 0.0)
    if kind == "bandit":
        lo_arm, hi_arm = _pct(data, "arm_lo", 0.94), _pct(data, "arm_hi", 1.12)
        step = _f(data, "arm_step", 2, 0.5, 10) / 100
        arms = [round(lo_arm + i * step, 4) for i in range(int(round((hi_arm - lo_arm) / step)) + 1)]
        if not 2 <= len(arms) <= 40:
            raise ValueError("จำนวนราคาที่ลอง (arms) ต้องอยู่ระหว่าง 2–40")
        hist = [(k, y) for k, y, _ in research.parse_points(data.get("rows") or [])]
        return research.bandit(arms, hist, reserve=reserve, prior_p=_research_belief(data),
                               prior_strength=_f(data, "prior_strength", 2, 0, 50))
    raise ValueError("เลือกเครื่องมือก่อน")


def handle(data: dict) -> dict:
    kind = data.get("kind")
    if kind in ("bidcurve", "dp", "bandit"):
        return research_tool(data)
    if kind == "sheet":
        return sheet(data)
    if kind == "round1":
        return round1(data)
    if kind == "agent":
        return agent_status(data)
    if kind == "preflight":
        return preflight(data)
    if kind == "kstar":
        return kstar(data)
    if kind == "plan":
        return plan_text(data)
    if kind == "simulate":
        return simulate(data)
    if kind == "grid":
        return grid(data)
    raise ValueError("เลือกเครื่องมือก่อน")
