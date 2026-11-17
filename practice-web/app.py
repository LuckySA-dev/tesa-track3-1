"""Local Day 1 training UI. Run with Python 3.10+."""
import argparse
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from fractions import Fraction
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import RLock
from time import monotonic
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "vendor" / "TesaMarket-build"))
sys.path.insert(0, str(HERE.parent / "agent"))  # market_core, pricing, planner, strategy
sys.path.insert(0, str(HERE.parent / "tools"))  # sim, manual_helper, arena

from tesamarket import bots, domain
from tesamarket.errors import MarketError
from tesamarket.rules import dec, meets, money, ruleset, true_value

import strategy_tools

PLAYER = "YOU"
RULES = ruleset("day17")
BAG = RULES["bag_kg"]
BASE = dec(RULES["salvage_ratio"])
CENT = Decimal(".01")
PASS = {"ACCEPTED", "PARTIAL", "OUTBID"}


def decimal_input(value, title, low, high, exclusive=False):
    try:
        number = dec(value)
        if number.is_finite() and low <= number <= high and (not exclusive or number > low):
            return number
    except (ArithmeticError, ValueError, TypeError):
        pass
    raise ValueError(f"ตรวจค่า {title} ({low}–{high})")


def integer_input(value, title, low, high):
    number = decimal_input(value, title, low, high)
    if number != int(number):
        raise ValueError(f"{title} ต้องเป็นจำนวนเต็ม")
    return int(number)


def valuation(sample, bags=None):
    defect = dec(sample["defect_pct"])
    grade = next(step for step in RULES["defect_steps"] if defect <= step["max_defect_pct"])
    screen = min(18, max(12, sample["screen"]))
    g = (1 - defect / 100) * dec(grade["step"])
    m = true_value(RULES, {**sample, "defect_pct": 0, "screen": 14}) / (RULES["p0"] * dec(RULES["defect_steps"][0]["step"]))
    tv = true_value(RULES, sample)
    reserve = BASE * tv
    return {
        "grade": grade["grade"], "g": float(g), "m": float(m),
        "s": RULES["screen_factors"][str(screen)], "tv": money(tv), "tv_raw": format(tv.normalize(), "f"),
        "reserve": float(reserve), "min_positive": float(reserve.quantize(CENT, rounding=ROUND_FLOOR) + CENT),
        "lot_value": money(tv * bags * BAG) if bags is not None else None,
    }


def quote_metrics(tv, price, bags):
    return {
        "ratio": float(price / tv),
        "revenue": money(price * bags * BAG),
        "uplift": money((price - BASE * tv) * bags * BAG),
        "uplift_per_bag": money((price - BASE * tv) * BAG),
    }


def score_tool(data):
    total = decimal_input(data.get("lot_value"), "TV รวม", 0, 10**10, exclusive=True)
    revenue = decimal_input(data.get("revenue"), "รายได้ขายแล้ว", 0, 10**10)
    remaining = decimal_input(data.get("remaining_value"), "TV ของเหลือ", 0, total)
    pr = (revenue + BASE * remaining) / total
    leader = data.get("leader_pr")
    best = max(pr, decimal_input(leader, "PR ผู้นำ (%)", 0, 10**8) / 100) if leader not in (None, "") else None
    settings = RULES["scoring"]
    sell = None
    if pr <= BASE:
        sell = 0.0
    elif best is not None:
        sell = float(settings["pr_points"] * (pr - BASE) / (best - BASE))
    errors = data.get("appraisal", [])
    appraisal = 0.0
    mape = None
    if errors:
        if not isinstance(errors, list) or len(errors) != 4 or any(not isinstance(row, dict) for row in errors):
            raise ValueError("กรอกข้อมูลประเมินให้ครบ 4 ล็อต")
        weighted = Decimal(0)
        weight = 0
        for row in errors:
            tv = decimal_input(row.get("tv"), "TV จริง", 0, 10**6, exclusive=True)
            estimate = decimal_input(row.get("estimate"), "TV ที่ส่ง", 0, 10**6, exclusive=True)
            bags = integer_input(row.get("bags"), "กระสอบทั้งหมด", 1, 10**6)
            weighted += abs(estimate / tv - 1) * bags
            weight += bags
        mape = weighted / weight
        appraisal = float(settings["appraisal_points"] * max(0, 1 - mape / dec(settings["appraisal_zero_at_mape"])))
    return {"pr": float(pr), "salvage": money(BASE * remaining), "uplift": money(revenue - BASE * (total - remaining)),
            "mape": float(mape) if mape is not None else None, "pr_score": sell, "appraisal_score": appraisal,
            "total": sell + appraisal if sell is not None else None}


def calculator(data):
    action = data.get("kind")
    if action == "valuation":
        sample = {
            "moisture_pct": decimal_input(data.get("moisture"), "ความชื้น (%)", 0, 100),
            "defect_pct": decimal_input(data.get("defect"), "เมล็ดเสีย (%)", 0, 100),
            "screen": integer_input(data.get("screen"), "เบอร์ตะแกรง", 1, 1000),
        }
        bags = data.get("bags")
        return valuation(sample, integer_input(bags, "กระสอบ", 1, 10**6) if bags not in (None, "") else None)
    if action == "score":
        return score_tool(data)
    if action == "price":
        tv = decimal_input(data.get("tv"), "TV ดิบ", 0, 10**6, exclusive=True)
        bags = integer_input(data.get("bags"), "จำนวนกระสอบที่คาดว่าจะขายได้", 1, 10**6)
        low = data.get("passed")
        high = data.get("rejected")
        lower = decimal_input(low, "ราคา ÷ TV ที่ผ่าน (%)", 0, 10**8, exclusive=True) / 100 if low not in (None, "") else None
        upper = decimal_input(high, "ราคา ÷ TV ที่ถูกตัด (%)", 0, 10**8, exclusive=True) / 100 if high not in (None, "") else None
        price = decimal_input(data.get("price"), "ราคาเสนอ", 0, 1000, exclusive=True)
        if price != price.quantize(CENT):
            raise ValueError("ราคาต้องมีทศนิยมไม่เกิน 2 ตำแหน่ง")
        reserve = tv * BASE
        conflict = lower is not None and upper is not None and lower >= upper
        return {"reserve": float(reserve), "min_positive": float(reserve.quantize(CENT, rounding=ROUND_FLOOR) + CENT),
                "past_pass_price": float((tv * lower).quantize(CENT, rounding=ROUND_FLOOR)) if lower and not conflict else None,
                "past_cut_price": float(tv * upper) if upper and not conflict else None,
                "conflict": conflict, **quote_metrics(tv, price, bags)}
    raise ValueError("เลือกเครื่องมือคำนวณก่อน")


class Training:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.lock = RLock()
        self.room = None
        self.initial = None

    # Timelines from slide 14 (day 17): get ready 5 s -> appraisal 10 min -> 6 rounds -> 10 s matching each -> closed.
    # (setup_s, appraisal_s, open_s, clearing_s)
    TIMELINES = {
        "practice": (5, 600, 180, 10),     # ตลาดฝึก: รอบละ 3 นาที
        "scored": (5, 600, 480, 10),       # รอบเก็บคะแนน 17 พ.ย.: รอบละ 8 นาที
        "fast": (3, 120, 60, 3),           # ซ้อมเร็ว
    }
    ALIASES = {"timed": "scored"}

    def start(self, mode, replay=False):
        mode = self.ALIASES.get(mode, mode)
        if mode not in {"guided", *self.TIMELINES}:
            raise ValueError("เลือกโหมดฝึกก่อนเริ่ม")
        now = self.clock()
        if replay and self.initial is None:
            raise ValueError("ยังไม่มีโจทย์เดิม")
        if replay:
            self.room = deepcopy(self.initial)
        else:
            rules = ruleset("day17")
            self.room = {"preset": "day17", "mode": "solo", "rules": rules, "teams": {
                PLAYER: {"name": "ทีมของคุณ", "bot": False},
                **{f"B{i:02}": {"name": f"บอท {i:02}", "bot": True} for i in range(1, 20)},
            }}
            domain.new_game(self.room, now)
            self.initial = deepcopy(self.room)
        self.mode = mode
        self.run_id = uuid4().hex
        self.revealed = False
        timeline = self.room["rules"]["timeline"]
        if mode == "guided":
            domain.transition(self.room, "APPRAISAL", now, timeline["appraisal_s"])
            self.play_bots(now)
        else:
            setup, appraisal, open_s, clearing = self.TIMELINES[mode]
            timeline.update(setup_s=setup, appraisal_s=appraisal, open_s=open_s, clearing_s=clearing)
            domain.transition(self.room, "SETUP", now, setup)     # ล็อตขึ้นในหน้าเว็บ 5 วินาที แล้วเริ่มประเมิน

    def pause(self):
        """หยุดนาฬิกาชั่วคราว (ระหว่างหยุด ยื่น/แก้ซองไม่ได้ เหมือนตลาดจริงที่ผู้จัดกดหยุด)"""
        if self.mode == "guided":
            raise ValueError("โหมดฝึกทีละขั้นไม่มีนาฬิกา")
        room = self.room
        if room["paused_left"] is not None or room["deadline"] is None:
            return
        room["paused_left"] = max(0.0, room["deadline"] - self.clock())
        room["deadline"] = None

    def resume(self):
        room = self.room
        if room["paused_left"] is None:
            return
        room["deadline"] = self.clock() + room["paused_left"]
        room["paused_left"] = None

    def skip(self):
        """ข้ามไปช่วงถัดไปทันที (ใช้ซ้อม ไม่มีในตลาดจริง)"""
        if self.mode == "guided":
            return self.next_phase()
        self.resume()
        if self.room["deadline"] is not None:
            self.room["deadline"] = self.clock()
            self.tick()

    def play_bots(self, now):
        room = self.room
        if room["phase"] not in {"APPRAISAL", "ROUND_OPEN"}:
            return
        for team, profile in room["teams"].items():
            if not profile["bot"]:
                continue
            endpoint, payload = bots.proposal(domain.state(room, team, now), room["rules"])
            if endpoint == "appraisals":
                domain.appraisal(room, team, payload)
            elif endpoint == "offers:batch":
                domain.submit_offers(room, team, payload["offers"], batch=True, replace=True)

    def tick(self):
        if self.room is None or self.mode == "guided":
            return
        now = self.clock()
        while self.room["deadline"] is not None and now >= self.room["deadline"]:
            deadline = self.room["deadline"]
            domain.advance(self.room, deadline)
            self.play_bots(deadline)

    def next_phase(self):
        if self.mode != "guided":
            raise ValueError("โหมดจับเวลาจะเลื่อนไปรอบถัดไปเมื่อหมดเวลา")
        phase = self.room["phase"]
        if phase not in {"APPRAISAL", "ROUND_OPEN"}:
            raise ValueError("เกมนี้ปิดตลาดแล้ว")
        now = self.clock()
        self.room["deadline"] = now
        domain.advance(self.room, now)
        if phase == "ROUND_OPEN":
            self.room["deadline"] = now
            domain.advance(self.room, now)
        self.play_bots(now)

    def history(self):
        room = self.room
        return [domain.result_for(room, PLAYER, key) for key in room["game"]["results"]]

    def report(self):
        self.tick()
        if self.room is None:
            return {"phase": "READY", "can_replay": False}
        room = self.room
        state = domain.state(room, PLAYER, self.clock())
        history = self.history()
        lots = {lot["lot_id"]: lot for lot in state["lots"]}
        values = {key: true_value(room["rules"], lot["sample"]) for key, lot in lots.items()}
        total_tv = sum(values[key] * lot["kg_total"] for key, lot in lots.items())
        estimates = room["game"]["appraisals"].get(PLAYER, {})
        for key, lot in lots.items():
            lot["calc"] = valuation(lot["sample"], lot["kg_total"] // BAG)
            lot["estimate"] = estimates.get(key, {}).get("est_value_per_kg")
        observed = {}
        for result in history:
            names = {row["rfq_id"]: row["buyer"] for row in result["market_summary"]}
            for offer in result["my_offers"]:
                key = offer["lot_id"]
                ratio = Fraction(dec(offer["price_per_kg"])) / Fraction(values[key])
                offer["buyer"] = names[offer["rfq_id"]]
                offer.update(quote_metrics(values[key], dec(offer["price_per_kg"]), offer["filled_kg"] // BAG))
                evidence = observed.setdefault(offer["buyer"], {"passed": [], "rejected": []})
                if offer["status"] in PASS:
                    evidence["passed"].append(ratio)
                elif offer["status"] == "ABOVE_LIMIT":
                    evidence["rejected"].append(ratio)
        for q in state["rfqs"]:
            evidence = observed.get(q["buyer"], {"passed": [], "rejected": []})
            low, high = max(evidence["passed"], default=None), min(evidence["rejected"], default=None)
            conflict = low is not None and high is not None and low >= high
            q["evidence"] = {"passed": float(low) if low is not None else None,
                             "rejected": float(high) if high is not None else None, "conflict": conflict}
            q["options"] = []
            for key, lot in lots.items():
                tv = values[key]
                past = min(dec(1000), dec((low * Fraction(tv) * 100) // 1) / 100) if low and not conflict else None
                cut = high * Fraction(tv) if high and not conflict else None
                q["options"].append({
                    "lot_id": key, "eligible": meets(lot["sample"], q["spec"]),
                    "max_bags": min(lot["kg_available"], q["kg"]) // BAG,
                    "past_price": float(past) if past is not None and past > BASE * tv else None,
                    "cut_price": float(cut) if cut is not None else None,
                })
        for offer in state["my_open_offers"]:
            offer["ratio"] = float(dec(offer["price_per_kg"]) / values[offer["lot_id"]])
        stats = state["standings"]
        remaining_tv = sum(values[key] * lot["kg_remaining"] for key, lot in lots.items())
        stats["projected_pr"] = float((dec(stats["revenue_thb"]) + BASE * remaining_tv) / total_tv)
        stats["salvage_thb"] = money(BASE * remaining_tv)
        stats["total_tv"] = money(total_tv)
        stats["total_tv_raw"] = str(total_tv)
        stats["uplift"] = money(dec(stats["revenue_thb"]) - BASE * (total_tv - remaining_tv))
        state.update(mode=self.mode, run_id=self.run_id, history=history, unit=BAG, can_replay=True,
                     revealed=self.revealed, timeline=room["rules"]["timeline"])
        if self.mode == "guided":
            state["seconds_left"] = None
        if state["phase"] == "CLOSED":
            revealed = domain.reveal(room, PLAYER)["score"]
            state["score"] = {key: float(value) if value is not None else None for key, value in revealed.items()}
            rankings = domain.ranks(room, include_score=True)
            state["rank"] = next(row["rank"] for row in rankings if row["team_id"] == PLAYER)
            state["best_pr"] = max(row["pr"] for row in rankings)
        return state

    @staticmethod
    def config(data):
        return strategy_tools.plan_config(data)

    def compare(self, data):
        if self.room["phase"] != "ROUND_OPEN":
            raise ValueError("เปิดเปรียบเทียบเมื่อถึงรอบขาย")
        state = self.report()
        if state["phase"] != "ROUND_OPEN":
            raise ValueError("รอบนี้ปิดรับซองแล้ว")
        markup = data.get("markup")
        multiplier = (decimal_input(markup, "ราคา ÷ TV (%)", 0, 10**8, exclusive=True) / 100
                      if markup not in (None, "") else None)
        lots = {lot["lot_id"]: lot for lot in state["lots"]}
        candidates = []
        for buyer in state["rfqs"]:
            for option in buyer["options"]:
                if not option["eligible"] or not option["max_bags"]:
                    continue
                lot = lots[option["lot_id"]]
                tv = dec(lot["calc"]["tv_raw"])
                if multiplier is not None:
                    price = (tv * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)
                elif option["past_price"] is not None:
                    price = dec(option["past_price"])
                else:
                    continue
                if not 0 < price <= 1000:
                    continue
                metrics = quote_metrics(tv, price, option["max_bags"])
                cut = option["cut_price"]
                candidates.append({
                    **metrics, "buyer": buyer["buyer"], "rfq_id": buyer["rfq_id"],
                    "lot_id": lot["lot_id"], "bags": option["max_bags"], "price": float(price),
                    "above_past_cut": cut is not None and price >= dec(cut) - dec("1e-12"),
                    "evidence_conflict": buyer["evidence"]["conflict"],
                    "delta_pr": float((price - BASE * tv) * option["max_bags"] * BAG /
                                      dec(state["standings"]["total_tv_raw"])),
                })
        candidates.sort(key=lambda row: (row["uplift"] <= 0 or row["above_past_cut"], -row["uplift"]))
        return {"candidates": candidates}

    def act(self, data):
        action = data.get("action")
        if action in {"start", "replay"}:
            self.start(data.get("mode"), replay=action == "replay")
            return self.report()
        if action == "reset":                       # back to the start screen; the last lots stay for "เล่นโจทย์เดิม"
            self.room = None
            return {"phase": "READY", "can_replay": self.initial is not None}
        self.tick()
        if self.room is None:
            raise ValueError("เริ่มตลาดฝึกก่อน")
        if action in {"pause", "resume", "skip", "reveal"}:      # clock controls: valid whatever the phase
            if data.get("run_id") != self.run_id:
                raise ValueError("เกมเปลี่ยนแล้ว กรุณาโหลดหน้าใหม่")
            if action == "reveal":
                if self.room["phase"] != "CLOSED":
                    raise ValueError("ดูมูลค่าจริงได้หลังปิดตลาด")
                self.revealed = True
            else:
                getattr(self, action)()
            return self.report()
        current = self.room["game"]["round"]
        if (data.get("run_id") != self.run_id or data.get("phase") != self.room["phase"]
                or data.get("round_id") != (current["round_id"] if current else None)):
            raise ValueError("สถานะตลาดเปลี่ยนแล้ว กรุณาลองใหม่ในรอบปัจจุบัน")
        if action == "compare":
            return self.compare(data)
        if action == "recommend":
            return strategy_tools.recommend(self.report(), self.config(data))
        if action == "apply_plan":
            rec = strategy_tools.recommend(self.report(), self.config(data))
            offers = [row["api"] for row in rec["offers"]]
            if not offers:
                raise ValueError("ไม่มีซองที่แนะนำในรอบนี้")
            domain.submit_offers(self.room, PLAYER, offers, batch=True, replace=True)
            return self.report()
        if action == "next":
            self.next_phase()
        elif action == "appraise":
            domain.appraisal(self.room, PLAYER, data)
        elif action == "offer":
            offer = {**data, "kg": integer_input(data.get("bags"), "กระสอบ", 1, 1000) * BAG}
            domain.submit_offers(self.room, PLAYER, [offer])
        elif action == "edit":
            offer = {"kg": integer_input(data.get("bags"), "กระสอบ", 1, 1000) * BAG,
                     "price_per_kg": data.get("price_per_kg")}
            domain.edit_offer(self.room, PLAYER, data.get("offer_id"), offer)
        elif action == "cancel":
            domain.edit_offer(self.room, PLAYER, data.get("offer_id"))
        else:
            raise ValueError("ไม่พบการดำเนินการนี้")
        return self.report()


ERRORS = {
    "APPRAISAL_ALREADY_SUBMITTED": "ส่งประเมินแล้ว รอบนี้ส่งได้ครั้งเดียว",
    "EXCEEDS_REMAINING": "จำนวนที่เสนอเกินสต็อกที่ยังไม่ถูกจอง",
    "WRONG_PHASE": "ตลาดอยู่คนละช่วงกับรายการที่เลือก",
    "ROUND_CLOSED": "รอบนี้ปิดรับซองแล้ว",
    "UNKNOWN_RFQ": "ไม่พบคำขอซื้อในรอบนี้",
}
FILES = {"/": ("index.html", "text/html"), "/trade": ("index.html", "text/html"),
         "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css"),
         "/strategy.js": ("strategy.js", "text/javascript"), "/sheet.js": ("sheet.js", "text/javascript"), "/common.js": ("common.js", "text/javascript"),
         "/round1.js": ("round1.js", "text/javascript"),
         "/research": ("research.html", "text/html"), "/research.js": ("research.js", "text/javascript"), "/round2.js": ("round2.js", "text/javascript")}


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, game, **kwargs):
        self.game = game
        super().__init__(*args, **kwargs)

    def send_bytes(self, body, kind, status=200):
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), "application/json", status)

    def do_GET(self):
        if self.path == "/api/state":
            with self.game.lock:
                state = self.game.report()
            self.send_json(state)
        elif self.path in FILES:
            filename, kind = FILES[self.path]
            self.send_bytes((HERE / "web" / filename).read_bytes(), kind)
        else:
            self.send_json({"error": "ไม่พบหน้านี้"}, 404)

    def do_POST(self):
        if self.path not in {"/api/action", "/api/calculate", "/api/strategy"}:
            return self.send_json({"error": "ไม่พบหน้านี้"}, 404)
        host = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get("Origin") not in (None, host, host.replace("127.0.0.1", "localhost")):
            return self.send_json({"error": "ให้ใช้หน้าเว็บฝึกบนเครื่องนี้"}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "ข้อมูลต้องเป็น JSON"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32_768:
                raise ValueError("ข้อมูลมีขนาดไม่ถูกต้อง")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("ข้อมูลไม่ถูกต้อง")
            if self.path == "/api/calculate":
                result = calculator(data)
            elif self.path == "/api/strategy":
                result = strategy_tools.handle(data)
            else:
                with self.game.lock:
                    result = self.game.act(data)
        except MarketError as error:
            payload = error.payload
            self.send_json({"error": ERRORS.get(payload["code"], payload["message"]), "code": payload["code"]}, error.status)
        except (ValueError, TypeError, ArithmeticError) as error:
            self.send_json({"error": str(error)}, 400)
        else:
            self.send_json(result)

    def log_message(self, template, *args):
        if self.path != "/api/state":
            super().log_message(template, *args)


def main():
    parser = argparse.ArgumentParser(description="TESA Market Day 1 training")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, game=Training())) as server:
        print(f"Day 1: http://127.0.0.1:{server.server_port}/trade", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
