"""OFFLINE price helper for 17 Nov (round 1: offers must be typed BY HAND into /trade).

This file makes NO network calls and submits nothing. You type what the web page shows into a text file,
run this, and type the recommended prices into /trade yourself.

    python tools/manual_helper.py --init            # create data/manual/day1.txt (template)
    python tools/manual_helper.py                   # read data/manual/day1.txt, print appraisal + offers to type
    python tools/manual_helper.py my.txt --floor 0.86 --patience 0.6

Text format (one item per line, # = comment, numbers like the web page shows them):

    ROUNDS 6
    FESTIVAL 3 4                         optional: festival round numbers from the calendar
    LOT  A  5  1.79 13.23 14            LOT <id> <total bags> <defect %> <moisture %> <screen>
    ROUND 1                              start of a round section
    CAP  -                               optional: capacity_left_kg (Day 18), - = none
    RFQ  R1-01 COM 9 สยามคอมเมิร์ซ       RFQ <rfq_id> <COM|INS|SPEC> <bags> <buyer name ...>
    SPEC R1-01 7 12.5 -                  optional: max defect, max moisture, min screen (- = none)
    -- after the round clears, add what /trade shows: --
    RESULT R1-01 A 125.00 ABOVE_LIMIT 0  RESULT <rfq> <lot> <price> <status> <filled bags>
    MARKET R1-01 9 6 14 118.50 126.00    MARKET <rfq> <wanted bags> <bought bags> [offers] [min] [max]
    ROUND 2
    ...

The plan is made for the LAST round section. Stock = total bags - filled bags of all RESULT lines.
Every run is appended to manual/round_log.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))  # agent modules

import market_core as mc
from planner import PlanConfig, plan_round, plan_table
from pricing import CeilingLearner, norm_type

HERE = Path(__file__).resolve().parent
DEFAULT_FILE = HERE.parent / "data" / "manual" / "day1.txt"
LOG_FILE = HERE.parent / "data" / "manual" / "round_log.md"

DEFAULT_SPECS = {
    "COMMERCIAL": {"max_defect_pct": 7, "max_moisture_pct": 12.5, "min_screen": None},
    "INSTANT": {"max_defect_pct": 15, "max_moisture_pct": 14, "min_screen": None},
    # Specialty numbers are NOT published for Day 17 - always add a SPEC line for it.
    "SPECIALTY": {"max_defect_pct": 3, "max_moisture_pct": 12.2, "min_screen": 16},
}

TEMPLATE = """# Day 1 manual helper input - fill from the /trade page. Lines starting with # are ignored.
ROUNDS 6
# FESTIVAL 3 4      <- uncomment and set if the calendar shows festival rounds
# LOT <id> <total bags> <defect %> <moisture %> <screen>
LOT A 5 1.79 13.23 14
LOT B 6 3.40 11.59 16
LOT C 7 5.56 12.95 13
LOT D 8 6.02 14.05 16

ROUND 1
# RFQ <rfq_id> <COM|INS|SPEC> <bags> <buyer name>
RFQ R1-01 COM 9 สยามคอมเมิร์ซ
RFQ R1-02 INS 6 ทรีอินวันฟู้ดส์
RFQ R1-03 COM 13 ชุมพรคั่วบด
RFQ R1-04 INS 5 อินสแตนท์ไทย
# after clearing add e.g.:
# RESULT R1-01 A 125.00 ABOVE_LIMIT 0
# MARKET R1-01 9 6 14 118.50 126.00
"""


def _num(x):
    return None if x in ("-", "", None) else float(x)


def parse_text(text: str) -> dict:
    """Parse the text format into {"rounds_total", "lots", "rounds": [{number, cap, rfqs, results, market}]}"""
    game = {"rounds_total": 6, "lots": {}, "rounds": [], "errors": [], "festival": set()}
    cur = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        p = line.split()
        tag = p[0].upper()
        try:
            if tag == "ROUNDS":
                game["rounds_total"] = int(p[1])
            elif tag == "FESTIVAL":
                game["festival"] |= {int(x) for x in p[1:]}
            elif tag == "LOT":
                game["lots"][p[1]] = {"lot_id": p[1], "bags": int(p[2]),
                                      "sample": {"defect_pct": float(p[3]), "moisture_pct": float(p[4]),
                                                 "screen": int(float(p[5]))}}
            elif tag == "ROUND":
                cur = {"number": int(p[1]), "cap": None, "rfqs": {}, "results": [], "market": {}}
                game["rounds"].append(cur)
            elif cur is None:
                raise ValueError("put a ROUND line before RFQ/RESULT/MARKET/CAP lines")
            elif tag == "CAP":
                cur["cap"] = None if p[1] == "-" else int(float(p[1]))
            elif tag == "RFQ":
                btype = norm_type(p[2])
                cur["rfqs"][p[1]] = {"rfq_id": p[1], "buyer_type": btype, "bags": int(p[3]),
                                     "kg": int(p[3]) * mc.BAG_KG, "buyer": " ".join(p[4:]) or btype,
                                     "spec": dict(DEFAULT_SPECS.get(btype, DEFAULT_SPECS["INSTANT"]))}
            elif tag == "SPEC":
                cur["rfqs"][p[1]]["spec"] = {"max_defect_pct": _num(p[2]), "max_moisture_pct": _num(p[3]),
                                             "min_screen": None if p[4] == "-" else int(float(p[4]))}
            elif tag == "RESULT":
                cur["results"].append({"rfq_id": p[1], "lot_id": p[2], "price_per_kg": float(p[3]),
                                       "status": p[4].upper(), "filled_kg": int(float(p[5])) * mc.BAG_KG})
            elif tag == "MARKET":
                cur["market"][p[1]] = {"rfq_id": p[1], "wanted_kg": int(p[2]) * mc.BAG_KG,
                                       "bought_kg": int(p[3]) * mc.BAG_KG,
                                       "offers": int(p[4]) if len(p) > 4 and p[4] != "-" else None,
                                       "price_min": _num(p[5]) if len(p) > 5 else None,
                                       "price_max": _num(p[6]) if len(p) > 6 else None}
            else:
                raise ValueError(f"unknown line type {tag}")
        except (IndexError, ValueError, KeyError) as e:
            game["errors"].append(f"line {n}: {raw.strip()}  ->  {e}")
    return game


def game_from_dict(d: dict) -> dict:
    """Build the same game structure from JSON (used by the web Round-1 page).

    d = {"rounds_total": 6, "festival": [3, 4],
         "lots": [{"lot_id", "bags", "defect_pct", "moisture_pct", "screen"}],
         "rounds": [{"number", "cap", "rfqs": [{"rfq_id", "buyer", "buyer_type", "bags", "spec"?}],
                     "results": [{"rfq_id", "lot_id", "price_per_kg", "status", "filled_bags"}],
                     "market": [{"rfq_id", "wanted_bags", "bought_bags", "offers", "price_min", "price_max"}]}]}
    """
    game = {"rounds_total": int(d.get("rounds_total") or 6), "lots": {}, "rounds": [], "errors": [],
            "festival": {int(x) for x in d.get("festival") or []}}
    for l in d.get("lots") or []:
        try:
            if not str(l.get("lot_id", "")).strip():
                continue
            game["lots"][str(l["lot_id"]).strip()] = {
                "lot_id": str(l["lot_id"]).strip(), "bags": int(float(l["bags"])),
                "sample": {"defect_pct": float(l["defect_pct"]), "moisture_pct": float(l["moisture_pct"]),
                           "screen": int(float(l["screen"]))}}
        except (KeyError, TypeError, ValueError) as e:
            game["errors"].append(f"lot {l.get('lot_id', '?')}: {e}")
    for r in d.get("rounds") or []:
        cur = {"number": int(r["number"]), "cap": int(r["cap"]) if r.get("cap") not in (None, "") else None,
               "rfqs": {}, "results": [], "market": {}}
        for q in r.get("rfqs") or []:
            try:
                if not str(q.get("rfq_id", "")).strip() or q.get("bags") in (None, ""):
                    continue                                   # row still being typed: ignore quietly
                btype = norm_type(q.get("buyer_type"))
                spec = q.get("spec") or DEFAULT_SPECS.get(btype, DEFAULT_SPECS["INSTANT"])
                spec = {"max_defect_pct": _num(spec.get("max_defect_pct")),
                        "max_moisture_pct": _num(spec.get("max_moisture_pct")),
                        "min_screen": int(float(spec["min_screen"])) if spec.get("min_screen") not in (None, "", "-") else None}
                cur["rfqs"][q["rfq_id"]] = {"rfq_id": q["rfq_id"], "buyer_type": btype, "bags": int(float(q["bags"])),
                                            "kg": int(float(q["bags"])) * mc.BAG_KG,
                                            "buyer": (q.get("buyer") or btype).strip(), "spec": spec}
            except (KeyError, TypeError, ValueError) as e:
                game["errors"].append(f"RFQ {q.get('rfq_id', '?')}: {e}")
        for o in r.get("results") or []:
            try:
                if not o.get("status") or o.get("price_per_kg") in (None, ""):
                    continue                                   # result row not finished yet

                cur["results"].append({"rfq_id": o["rfq_id"], "lot_id": o["lot_id"],
                                       "price_per_kg": float(o["price_per_kg"]), "status": str(o["status"]).upper(),
                                       "filled_kg": int(float(o.get("filled_bags") or 0)) * mc.BAG_KG})
            except (KeyError, TypeError, ValueError) as e:
                game["errors"].append(f"result {o.get('rfq_id', '?')}: {e}")
        for m in r.get("market") or []:
            try:
                if m.get("wanted_bags") in (None, "") or m.get("bought_bags") in (None, ""):
                    continue
                cur["market"][m["rfq_id"]] = {
                    "rfq_id": m["rfq_id"], "wanted_kg": int(float(m["wanted_bags"])) * mc.BAG_KG,
                    "bought_kg": int(float(m["bought_bags"])) * mc.BAG_KG,
                    "offers": int(float(m["offers"])) if m.get("offers") not in (None, "") else None,
                    "price_min": _num(m.get("price_min")), "price_max": _num(m.get("price_max"))}
            except (KeyError, TypeError, ValueError) as e:
                game["errors"].append(f"market {m.get('rfq_id', '?')}: {e}")
        game["rounds"].append(cur)
    return game


def to_text(game: dict) -> str:
    """Export a game structure back to the text format (backup / manual_helper.py on the command line)."""
    short = {"COMMERCIAL": "COM", "INSTANT": "INS", "SPECIALTY": "SPEC"}
    out = [f"ROUNDS {game['rounds_total']}"]
    if game.get("festival"):
        out.append("FESTIVAL " + " ".join(str(x) for x in sorted(game["festival"])))
    for l in game["lots"].values():
        s = l["sample"]
        out.append(f"LOT {l['lot_id']} {l['bags']} {s['defect_pct']} {s['moisture_pct']} {s['screen']}")
    for r in game["rounds"]:
        out.append(f"\nROUND {r['number']}")
        if r.get("cap") is not None:
            out.append(f"CAP {r['cap']}")
        for q in r["rfqs"].values():
            out.append(f"RFQ {q['rfq_id']} {short.get(q['buyer_type'], q['buyer_type'])} {q['bags']} {q['buyer']}")
            sp, default = q["spec"], DEFAULT_SPECS.get(q["buyer_type"])
            if sp != default:
                ms = "-" if sp["min_screen"] is None else sp["min_screen"]
                out.append(f"SPEC {q['rfq_id']} {sp['max_defect_pct']} {sp['max_moisture_pct']} {ms}")
        for o in r["results"]:
            out.append(f"RESULT {o['rfq_id']} {o['lot_id']} {o['price_per_kg']:.2f} {o['status']} {o['filled_kg'] // mc.BAG_KG}")
        for m in r["market"].values():
            extra = " ".join("-" if m.get(k) is None else str(m[k]) for k in ("offers", "price_min", "price_max"))
            out.append(f"MARKET {m['rfq_id']} {m['wanted_kg'] // mc.BAG_KG} {m['bought_kg'] // mc.BAG_KG} {extra}")
    return "\n".join(out) + "\n"


def advise(game: dict, cfg: PlanConfig | None = None) -> dict:
    """Learn from every finished round, then plan the last round. Pure: no files, no network."""
    cfg = cfg or PlanConfig()
    lots = game["lots"]
    tv = {k: mc.tv(l["sample"]) for k, l in lots.items()}
    samples = {k: l["sample"] for k, l in lots.items()}
    learner = CeilingLearner()
    sold = {k: 0 for k in lots}
    revenue = 0.0
    known_types = {}
    for rnd in game["rounds"]:
        for r in rnd["rfqs"].values():
            known_types.setdefault(r["buyer_type"], r["spec"])
        if not rnd["results"] and not rnd["market"]:
            continue
        summary = []
        for rid, r in rnd["rfqs"].items():
            m = rnd["market"].get(rid)
            if m:
                summary.append({**m, "buyer": r["buyer"], "buyer_type": r["buyer_type"]})
        offers = [o for o in rnd["results"] if o["lot_id"] in lots]
        for o in offers:
            r = rnd["rfqs"].get(o["rfq_id"], {})
            o.setdefault("buyer", r.get("buyer", "?"))
            o.setdefault("buyer_type", r.get("buyer_type", "?"))
            sold[o["lot_id"]] += o["filled_kg"]
            revenue += o["filled_kg"] * o["price_per_kg"]
        # offers carry buyer/buyer_type themselves, so RFQs without a MARKET line still teach the learner
        learner.observe({"round_id": f"R{rnd['number']}", "my_offers": offers, "market_summary": summary},
                        tv, samples, {rid: r["spec"] for rid, r in rnd["rfqs"].items()})

    state_lots = [{"lot_id": k, "kg_available": l["bags"] * mc.BAG_KG - sold[k], "sample": l["sample"],
                   "kg_total": l["bags"] * mc.BAG_KG} for k, l in lots.items()]
    cur = game["rounds"][-1] if game["rounds"] else None
    out = {"tv": tv, "lots": state_lots, "learner": learner, "revenue": revenue, "plan": None, "round": cur}
    pr_lots = [{"tv": tv[l["lot_id"]], "kg_total": l["kg_total"], "kg_remaining": l["kg_available"]} for l in state_lots]
    out["pr_now"] = mc.pr(revenue, pr_lots) if pr_lots else None
    if cur and cur["rfqs"] and not cur["results"]:
        out["plan"] = plan_round(state_lots, list(cur["rfqs"].values()), tv, learner, round_number=cur["number"],
                                 rounds_total=game["rounds_total"], capacity_left_kg=cur["cap"], cfg=cfg,
                                 known_types=known_types,
                                 labels={n: "festival" if n in game["festival"] else ""
                                         for n in range(1, game["rounds_total"] + 1)},
                                 progress={"revenue": revenue,
                                           "total_value": sum(tv[l["lot_id"]] * l["kg_total"] for l in state_lots)})
    return out


def report(game: dict, res: dict) -> str:
    L = []
    for e in game["errors"]:
        L.append("!! " + e)
    L.append("APPRAISAL - type est TV per kg (submit ONCE):")
    for k, l in game["lots"].items():
        b = mc.true_value(l["sample"])
        L.append(f"  {k:<8} {b['grade']:<15} G {b['G']:.4f} M {b['M']:.4f} S {b['S']:.2f}  TV = {b['tv_2dp']:.2f}"
                 f"   (0.80xTV = {0.8 * b['tv']:.2f}, min price {mc.min_price(b['tv']):.2f})")
    L.append("")
    stock = ", ".join(f"{l['lot_id']} {l['kg_available'] // 60}/{l['kg_total'] // 60}" for l in res["lots"])
    L.append(f"STOCK (bags left/total): {stock}   revenue {res['revenue']:,.2f}   PR if stopped now {res['pr_now']:.4f}")
    lr = res["learner"]
    if lr.by_name:
        L.append("CEILING EVIDENCE (k = price / TV):")
        for name, ev in lr.by_name.items():
            b = lr.belief(name, next((r["buyer_type"] for rnd in game["rounds"] for r in rnd["rfqs"].values()
                                      if r["buyer"] == name), "?"))
            fills = ", ".join(f"{f:.0%}" for f in ev.fills) or "-"
            cut = f"{min(ev.above):.3f}" if ev.above else "-"
            top = f"{max(ev.passed):.3f}" if ev.passed else "-"
            L.append(f"  {name[:20]:<20} passed max {top:<6} cut min {cut:<6} fill {fills:<14} -> belief {b.lo:.3f}-{b.hi:.3f}"
                     + (f" clear~{b.clear_k:.3f}" if b.clear_k else ""))
    L.append("")
    plan, cur = res["plan"], res["round"]
    if plan is None:
        if cur and cur["results"]:
            L.append(f"Round {cur['number']} already has RESULT lines - add a new 'ROUND {cur['number'] + 1}' section with its RFQs.")
        else:
            L.append("No RFQs in the last ROUND section yet.")
        return "\n".join(L)
    L.append(f"ROUND {cur['number']} of {game['rounds_total']} - OFFERS TO TYPE INTO /trade "
             f"(price is already rounded down so k never crosses the target):")
    L.append(plan_table(plan))
    for lot, v in plan.reserve.items():
        L.append(f"  hold value V[{lot}] = {v:.3f}  (only sell below this if it is the last round)")
    for lot in plan.unsellable:
        L.append(f"  !! {lot} passes NO buyer spec -> leave it (auto-sold at 0.80 x TV)")
    for lot, rfq, why in plan.skipped[:12]:
        L.append(f"  skip {lot} -> {rfq}: {why}")
    L.extend("  " + n for n in plan.notes)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Offline offer helper (no network).")
    ap.add_argument("file", nargs="?", default=str(DEFAULT_FILE))
    ap.add_argument("--init", action="store_true", help="write a template input file")
    ap.add_argument("--floor", type=float, default=0.85)
    ap.add_argument("--patience", type=float, default=0.6)
    ap.add_argument("--no-probe", action="store_true")
    a = ap.parse_args()
    path = Path(a.file)
    if a.init:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            print(f"{path} exists - not overwriting")
        else:
            path.write_text(TEMPLATE, encoding="utf-8")
            print(f"wrote {path}")
        return
    game = parse_text(path.read_text(encoding="utf-8"))
    res = advise(game, PlanConfig(floor=a.floor, patience=a.patience, probes=not a.no_probe))
    text = report(game, res)
    print(text)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n## {dt.datetime.now():%Y-%m-%d %H:%M:%S}  ({path.name})\n```\n{text}\n```\n")


if __name__ == "__main__":
    main()
