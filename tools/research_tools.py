"""Research-backed pricing tools (pure, stdlib only). Used by the /research page.

1. Bid-response curve (TAC SCM style)
   Agents in the Trading Agent Competition - Supply Chain Management bid on customer RFQs and the
   cheapest valid bid wins - the same sealed-offer structure as this market. Strong agents (e.g. TacTex,
   Pardoe & Stone) predicted P(win | price) from past results and chose the bid maximising expected profit.
   Here: fit P(sell | k) = 1 / (1 + exp(-(a + b k))) to (k, sold?) history by weighted logistic regression,
   then k* = argmax (k - V) x P(k).

2. Dynamic pricing of inventories (Gallego & van Ryzin 1994)
   Finite stock, finite horizon, unsold stock has a salvage value -> the optimal price depends on
   (stock left, time left), and the right "cost" of selling a unit now is the marginal value of keeping it,
   dV = V_t(n) - V_t(n-1). Here: exact DP over (bags left, round) with salvage 0.80 x TV at the end.
   dV is the hold value V the planner uses.

3. Online posted-price learning (Kleinberg & Leighton 2003; Besbes & Zeevi 2009; Ferreira, Simchi-Levi & Wang 2018)
   Unknown demand curve -> treat a grid of k values as bandit arms and balance learning vs earning.
   Here: Beta-Bernoulli Thompson sampling and UCB1 on reward (k - V) x P(sold | k).

All money is in k units (price / TV): a bag sold at k earns k x TV, an unsold bag is worth 0.80 x TV,
so V_1(n) / n is directly the PR you can expect for that stock.
"""
from __future__ import annotations

import math
import random

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))  # agent modules

from pricing import Belief

SALVAGE = 0.80
SOLD = {"ACCEPTED", "PARTIAL"}
NOT_SOLD = {"ABOVE_LIMIT", "OUTBID"}


# ---------------------------------------------------------------------- 1. bid-response curve

def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def parse_points(rows) -> list[tuple[float, float, float]]:
    """rows: [{"k": 1.03 or 103, "status": "ACCEPTED"} | {"k", "won": 0/1}] -> [(k, won, weight)]."""
    pts = []
    for r in rows:
        k = float(r["k"])
        k = k / 100 if k > 3 else k                         # accept % input
        if "won" in r and r["won"] not in (None, ""):
            won = float(r["won"])
        else:
            st = str(r.get("status", "")).upper()
            if st not in SOLD | NOT_SOLD:
                continue                                    # SPEC_MISMATCH says nothing about price
            won = 1.0 if st in SOLD else 0.0
        pts.append((k, won, float(r.get("weight", 1.0))))
    return pts


def fit_logistic(points, prior: tuple[float, float] | None = (0.95, 1.10), prior_weight: float = 0.5,
                 l2: float = 1e-3, iters: int = 60) -> dict:
    """Weighted logistic regression P(sold|k) = sigmoid(a + b (k - 1)), Newton-Raphson.

    prior=(lo, hi) adds two weak pseudo-observations (sold at lo, not sold at hi) so the fit is defined
    even with 0-2 real points or perfectly separated data.
    """
    pts = list(points)
    if prior:
        pts += [(prior[0], 1.0, prior_weight), (prior[1], 0.0, prior_weight)]
    if not pts:
        raise ValueError("no data")
    a, b = 0.0, -10.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for k, y, w in pts:
            x = k - 1.0
            p = _sigmoid(a + b * x)
            r = w * (y - p)
            ga += r
            gb += r * x
            v = w * p * (1 - p)
            haa += v
            hab += v * x
            hbb += v * x * x
        ga -= l2 * a
        gb -= l2 * b
        haa += l2
        hbb += l2
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-9:
            break
    k50 = 1.0 - a / b if b else None
    return {"a": a, "b": b, "k50": k50, "n": len(points),
            "p": lambda k: _sigmoid(a + b * (k - 1.0))}


def bid_curve(rows, reserve: float = SALVAGE, floor: float = 0.85, prior=(0.95, 1.10), lo=0.80, hi=1.30,
              step: float = 0.005) -> dict:
    pts = parse_points(rows)
    fit = fit_logistic(pts, prior)
    best = {"k": floor, "p": fit["p"](floor), "ev": -1.0}
    curve = []
    k = max(lo, floor)
    while k <= hi + 1e-9:
        p = fit["p"](k)
        ev = (k - reserve) * p
        curve.append({"k": round(k, 4), "p": p, "ev": ev})
        if ev > best["ev"]:
            best = {"k": round(k, 4), "p": p, "ev": ev}
        k += step
    return {"a": fit["a"], "b": fit["b"], "k50": fit["k50"], "n": fit["n"], "best": best, "curve": curve,
            "points": [{"k": k, "won": y} for k, y, _ in pts]}


# ---------------------------------------------------------------------- 2. inventory DP (Gallego & van Ryzin)

def _binom_pmf(n: int, p: float) -> list[float]:
    p = min(max(p, 0.0), 1.0)
    out = [0.0] * (n + 1)
    for s in range(n + 1):
        out[s] = math.comb(n, s) * p ** s * (1 - p) ** (n - s)
    return out


def inventory_dp(bags: int, rounds: int, per_round: int, p_of_k, k_grid=None, salvage: float = SALVAGE,
                 festival: set | None = None, festival_shift: float = 0.0) -> dict:
    """Exact DP. State (n bags left, round t). Each round offer q = min(n, per_round) bags at one k;
    each bag sells independently with P(k) (shifted by festival_shift in festival rounds).

        V_{T+1}(n) = salvage x n
        V_t(n)     = max_k  sum_s Binom(s; q, P(k)) [ k s + V_{t+1}(n - s) ]

    Offering every bag you may is optimal whenever k > marginal hold value (it only adds upside), so the
    decision is k per state. Returns policy k*(n, t), V, marginal value dV(n, t) and expected PR.
    """
    if not (1 <= bags <= 80 and 1 <= rounds <= 12 and 1 <= per_round <= 80):
        raise ValueError("bags 1-80, rounds 1-12, per_round 1-80")
    k_grid = k_grid or [round(0.85 + 0.01 * i, 2) for i in range(36)]          # 0.85 .. 1.20
    festival = festival or set()
    V = [[0.0] * (bags + 1) for _ in range(rounds + 2)]
    K = [[None] * (bags + 1) for _ in range(rounds + 2)]
    P = [[None] * (bags + 1) for _ in range(rounds + 2)]
    for n in range(bags + 1):
        V[rounds + 1][n] = salvage * n
    for t in range(rounds, 0, -1):
        shift = festival_shift if t in festival else 0.0
        probs = [(k, p_of_k(k - shift)) for k in k_grid]
        for n in range(bags + 1):
            if n == 0:
                continue
            q = min(n, per_round)
            best_v, best_k, best_p = V[t + 1][n], None, 0.0             # option: offer nothing
            for k, p in probs:
                pmf = _binom_pmf(q, p)
                v = sum(pr * (k * s + V[t + 1][n - s]) for s, pr in enumerate(pmf))
                if v > best_v + 1e-12:
                    best_v, best_k, best_p = v, k, p
            V[t][n], K[t][n], P[t][n] = best_v, best_k, best_p
    marginal = [[(V[t][n] - V[t][n - 1]) if n else None for n in range(bags + 1)] for t in range(rounds + 2)]
    return {
        "bags": bags, "rounds": rounds, "per_round": per_round,
        "policy": [[K[t][n] for n in range(bags + 1)] for t in range(1, rounds + 1)],
        "p": [[P[t][n] for n in range(bags + 1)] for t in range(1, rounds + 1)],
        "value": [[V[t][n] for n in range(bags + 1)] for t in range(1, rounds + 1)],
        "marginal": [marginal[t] for t in range(1, rounds + 2)],
        "expected_pr": V[1][bags] / bags,
    }


def belief_p(lo: float, hi: float, clear_k: float | None = None, width: float = 0.02):
    b = Belief(lo=lo, hi=hi, clear_k=clear_k, width=width)
    return b.p_accept


# ---------------------------------------------------------------------- 3. posted-price bandit

def bandit(arms: list[float], history, reserve: float = SALVAGE, prior_p=None, prior_strength: float = 2.0,
           draws: int = 4000, seed: int = 7) -> dict:
    """Thompson sampling + UCB1 over price arms k.

    history: [(k, won)] - each result is credited to the nearest arm.
    prior_p(k): prior sale probability per arm (e.g. from the ceiling belief), worth `prior_strength` trials.
    Reward of an arm = (k - reserve) x P(sold | k).
    """
    arms = sorted(set(round(a, 4) for a in arms))
    if not arms:
        raise ValueError("no arms")
    wins = {a: 0.0 for a in arms}
    trials = {a: 0.0 for a in arms}
    for k, won in history:
        a = min(arms, key=lambda x: abs(x - k))
        wins[a] += won
        trials[a] += 1
    rng = random.Random(seed)
    alpha, beta = {}, {}
    for a in arms:
        p0 = prior_p(a) if prior_p else 0.5
        p0 = min(max(p0, 0.02), 0.98)
        alpha[a] = 1e-3 + p0 * prior_strength + wins[a]
        beta[a] = 1e-3 + (1 - p0) * prior_strength + trials[a] - wins[a]
    best_count = {a: 0 for a in arms}
    for _ in range(draws):
        best = max(arms, key=lambda a: (a - reserve) * rng.betavariate(alpha[a], beta[a]))
        best_count[best] += 1
    total = sum(trials.values())
    out = []
    for a in arms:
        mean = alpha[a] / (alpha[a] + beta[a])
        n = trials[a] + prior_strength
        bonus = math.sqrt(2 * math.log(max(total + prior_strength * len(arms), 2)) / n)
        out.append({"k": a, "trials": trials[a], "wins": wins[a], "p_mean": mean,
                    "reward_mean": (a - reserve) * mean,
                    "ucb": (a - reserve) * min(1.0, mean + bonus),
                    "p_best": best_count[a] / draws})
    thompson = max(out, key=lambda r: r["p_best"])
    ucb = max(out, key=lambda r: r["ucb"])
    greedy = max(out, key=lambda r: r["reward_mean"])
    return {"arms": out, "thompson": thompson["k"], "ucb": ucb["k"], "greedy": greedy["k"], "draws": draws}
