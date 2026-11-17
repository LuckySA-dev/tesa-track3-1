"""Pure market maths for TESA Top Gun Rally 2026 - Track 3 (no I/O, stdlib only).

    true_value(sample, rules=None)      -> dict with grade, G, M, S, tv (raw) and tv_2dp
    tv(sample, rules=None)              -> raw TV float (THB/kg)
    passes(sample, spec, margin=0)      -> bool, every spec item with an optional safety margin
    pass_probability(sample, spec, noise)-> 0..1 chance the TRUE quality passes (Day 18 noisy samples)
    appraisal_estimate(sample, noise)   -> best TV to submit when the sample is noisy (minimises MAPE)
    validate_offer(...)                 -> list of server error codes this offer would get ([] = ok)
    pr(...), pr_points(...), appraisal_points(...) -> scoring

`rules` is the dict from GET /v1/calendar -> "rules". When it is None the published Day 17 numbers are used,
so the helper works offline.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

BAG_KG = 60
SALVAGE = 0.80

DEFAULT_RULES = {
    "p0": 120,
    "defect_steps": [
        {"max_defect_pct": 1, "grade": "PREMIUM", "step": 1.08},
        {"max_defect_pct": 3, "grade": "GRADE_1", "step": 1.04},
        {"max_defect_pct": 5, "grade": "GRADE_2", "step": 1.00},
        {"max_defect_pct": 7, "grade": "GRADE_3", "step": 0.96},
        {"max_defect_pct": 15, "grade": "BELOW_STANDARD", "step": 0.85},
        {"max_defect_pct": 100, "grade": "REJECT", "step": 0.60},
    ],
    "moisture": {"ref": 12.5, "hard_limit": 15, "penalty_per_pp": 0.02, "hard_factor": 0.60},
    "screen_factors": {"12": 0.92, "13": 0.97, "14": 1.00, "15": 1.00, "16": 1.03, "17": 1.03, "18": 1.06},
    "bag_kg": BAG_KG,
    "salvage_ratio": SALVAGE,
}


def round2(x: float) -> float:
    """Round half-up to 2 decimals the way a person (and the web page) would."""
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- true value

def grade_of(defect_pct: float, rules: dict | None = None) -> tuple[str, float]:
    """First defect step whose limit is >= d (limits are inclusive). Above every limit -> last step."""
    steps = (rules or DEFAULT_RULES)["defect_steps"]
    for step in steps:
        if defect_pct <= step["max_defect_pct"]:
            return step["grade"], step["step"]
    return steps[-1]["grade"], steps[-1]["step"]


def g_factor(defect_pct: float, rules: dict | None = None) -> float:
    return (1 - defect_pct / 100) * grade_of(defect_pct, rules)[1]


def m_factor(moisture_pct: float, rules: dict | None = None) -> float:
    mo = (rules or DEFAULT_RULES)["moisture"]
    ref = mo["ref"]
    if moisture_pct <= ref:
        return 1.0
    if moisture_pct <= mo["hard_limit"]:
        return (1 - moisture_pct / 100) / (1 - ref / 100) - mo["penalty_per_pp"] * (moisture_pct - ref)
    return mo["hard_factor"]


def s_factor(screen: int, rules: dict | None = None) -> float:
    table = {int(k): v for k, v in (rules or DEFAULT_RULES)["screen_factors"].items()}
    lo, hi = min(table), max(table)
    return table[min(max(int(screen), lo), hi)]


def true_value(sample: dict, rules: dict | None = None) -> dict:
    """TV = P0 x G x M x S (THB/kg). Only the final value is rounded."""
    r = rules or DEFAULT_RULES
    d, m, s = float(sample["defect_pct"]), float(sample["moisture_pct"]), sample["screen"]
    grade, step = grade_of(d, r)
    g, mf, sf = (1 - d / 100) * step, m_factor(m, r), s_factor(s, r)
    raw = r["p0"] * g * mf * sf
    return {"grade": grade, "step": step, "G": g, "M": mf, "S": sf, "tv": raw, "tv_2dp": round2(raw)}


def tv(sample: dict, rules: dict | None = None) -> float:
    return true_value(sample, rules)["tv"]


# --------------------------------------------------------------------------- spec checks

def passes(sample: dict, spec: dict, margin: float | dict = 0.0) -> bool:
    """True when the sample meets every spec item (<= / >= are inclusive).

    margin keeps a safety gap from the spec edges for noisy samples (Day 18):
    a number applies to defect and moisture, or pass {"defect": .35, "moisture": .3, "screen": 0}.
    """
    if isinstance(margin, dict):
        md, mm, ms = margin.get("defect", 0), margin.get("moisture", 0), margin.get("screen", 0)
    else:
        md = mm = margin
        ms = 0
    if spec.get("max_defect_pct") is not None and sample["defect_pct"] + md > spec["max_defect_pct"]:
        return False
    if spec.get("max_moisture_pct") is not None and sample["moisture_pct"] + mm > spec["max_moisture_pct"]:
        return False
    if spec.get("min_screen") is not None and sample["screen"] - ms < spec["min_screen"]:
        return False
    return True


def _uniform_le(value: float, limit: float, noise: float) -> float:
    """P(true <= limit) when true ~ Uniform(value - noise, value + noise)."""
    if noise <= 0:
        return 1.0 if value <= limit else 0.0
    return min(1.0, max(0.0, (limit - (value - noise)) / (2 * noise)))


def pass_probability(sample: dict, spec: dict, noise: dict | None = None) -> float:
    """Chance that the TRUE lot meets the spec, given sample noise (uniform +-noise, independent).

    noise = {"defect": 0.35, "moisture": 0.3}. None/0 -> deterministic 0 or 1 (Day 17).
    Screen is assumed exact (it is a sieve size, not a lab %).
    """
    noise = noise or {}
    if spec.get("min_screen") is not None and sample["screen"] < spec["min_screen"]:
        return 0.0
    p = 1.0
    if spec.get("max_defect_pct") is not None:
        p *= _uniform_le(sample["defect_pct"], spec["max_defect_pct"], noise.get("defect", 0))
    if spec.get("max_moisture_pct") is not None:
        p *= _uniform_le(sample["moisture_pct"], spec["max_moisture_pct"], noise.get("moisture", 0))
    return p


def appraisal_estimate(sample: dict, rules: dict | None = None, noise: dict | None = None, steps: int = 41) -> float:
    """TV estimate (2 dp) that minimises expected absolute % error when the sample is noisy.

    With no noise this is just TV(sample). With noise we take the MEDIAN of TV over a grid of possible
    true qualities (median minimises E|est/tv - 1| closely enough), which matters near grade steps:
    e.g. a sample at d=3.05 might really be GRADE_1 (d<=3) and be worth ~4% more.
    """
    noise = noise or {}
    nd, nm = noise.get("defect", 0), noise.get("moisture", 0)
    if not nd and not nm:
        return true_value(sample, rules)["tv_2dp"]
    values = []
    for i in range(steps):
        d = max(0.0, sample["defect_pct"] + nd * (2 * i / (steps - 1) - 1))
        for j in range(steps):
            m = max(0.0, sample["moisture_pct"] + nm * (2 * j / (steps - 1) - 1))
            values.append(tv({"defect_pct": d, "moisture_pct": m, "screen": sample["screen"]}, rules))
    values.sort()
    # weighted median of |est - v| / v  ->  median of v under weights 1/v
    weights = [1 / v for v in values]
    half, acc = sum(weights) / 2, 0.0
    for v, w in zip(values, weights):
        acc += w
        if acc >= half:
            return round2(v)
    return round2(values[-1])


# --------------------------------------------------------------------------- offer validation

def validate_offer(offer: dict, *, phase: str, rfqs: list[dict], lots: list[dict],
                   capacity_left_kg: int | None = None, bag_kg: int = BAG_KG) -> list[str]:
    """Mirror the server's checks for POST /v1/offers. Returns error codes in the server's order ([] = ok).

    lots: our lots from /v1/state (lot_id, kg_available).  rfqs: this round's RFQs (rfq_id, bags or kg).
    """
    errors: list[str] = []
    if phase != "ROUND_OPEN":
        return ["WRONG_PHASE"]
    lot = next((l for l in lots if l["lot_id"] == offer.get("lot_id")), None)
    if lot is None:
        errors.append("NOT_YOUR_LOT")
    rfq = next((r for r in rfqs if r["rfq_id"] == offer.get("rfq_id")), None)
    if rfq is None:
        errors.append("UNKNOWN_RFQ")
    kg = offer.get("kg")
    if type(kg) is not int or kg <= 0:
        errors.append("INVALID_FIELD")
    else:
        if kg % bag_kg:
            errors.append("NOT_MULTIPLE_OF_BAG")
        rfq_kg = (rfq.get("kg") or rfq.get("bags", 0) * bag_kg) if rfq else None
        if rfq_kg is not None and kg > rfq_kg:
            errors.append("INVALID_FIELD")
        if lot is not None and kg > lot["kg_available"]:
            errors.append("EXCEEDS_REMAINING")
        if capacity_left_kg is not None and kg > capacity_left_kg:
            errors.append("OVER_ROUND_CAPACITY")
    price = offer.get("price_per_kg")
    if type(price) not in (int, float) or not 0 < price <= 1000 or round(price, 2) != price:
        errors.append("INVALID_FIELD")
    # keep each code once, in order
    return list(dict.fromkeys(errors))


def price_for(k: float, tv_value: float) -> float:
    """Price to type in (2 dp) for a target k = price / TV. Rounded DOWN so we never cross a ceiling by a cent."""
    cents = int(Decimal(repr(k * tv_value)) * 100 // 1)
    return cents / 100


def min_price(tv_value: float, floor_k: float = SALVAGE) -> float:
    """Smallest price strictly above floor_k x TV (anything at or below 0.80 x TV is worse than not selling)."""
    return (int(Decimal(repr(floor_k * tv_value)) * 100 // 1) + 1) / 100


# --------------------------------------------------------------------------- scoring

def pr(revenue: float, lots: list[dict]) -> float:
    """PR = (revenue + 0.80 x TV x unsold kg) / (TV x total kg).

    lots: [{"tv": raw tv, "kg_total": .., "kg_remaining": ..}]
    """
    total = sum(l["tv"] * l["kg_total"] for l in lots)
    salvage = sum(l["tv"] * l["kg_remaining"] for l in lots) * SALVAGE
    return (revenue + salvage) / total if total else SALVAGE


def delta_pr(k: float, tv_value: float, kg: int, total_tv_kg: float) -> float:
    """PR gained by selling kg at k x TV instead of leaving it for salvage."""
    return (k - SALVAGE) * tv_value * kg / total_tv_kg


def pr_points(my_pr: float, best_pr: float, max_points: float = 80) -> float:
    """0 at PR 0.80, max_points for the best team, linear in between."""
    if best_pr <= SALVAGE or my_pr <= SALVAGE:
        return 0.0
    return max_points * min(1.0, (my_pr - SALVAGE) / (best_pr - SALVAGE))


def mape(items: list[dict]) -> float:
    """kg-weighted MAPE. items: [{"est": submitted, "tv": true, "kg": kg_total}]"""
    w = sum(i["kg"] for i in items)
    return sum(abs(i["est"] / i["tv"] - 1) * i["kg"] for i in items) / w


def appraisal_points(mape_value: float, max_points: float = 20, zero_at: float = 0.10) -> float:
    return max_points * max(0.0, 1 - mape_value / zero_at)
