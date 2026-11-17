// /research page: bid-response curve (TAC SCM), inventory DP (Gallego & van Ryzin), bandit (Thompson / UCB).
import {$, call, esc, f0, f2, k3, num, pc, restore, save, table, valuation} from "./common.js";

const fields = id => Object.fromEntries([...$(id).querySelectorAll("input")].map(i =>
  [i.name, i.type === "checkbox" ? (i.checked ? "1" : "0") : i.value]));
function rows() {
  return $("history").value.split("\n").map(l => l.trim().split(/[\s,;]+/)).filter(p => p.length >= 2 && p[0] !== "")
    .map(([k, s]) => /^[01]$/.test(s) ? {k, won: s} : {k, status: s.toUpperCase()});
}
function fail(out, e) {
  out.classList.add("error");
  out.textContent = e.message;
}
const debounce = (fn, ms = 300) => { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; };

// ------------------------------------------------------------------ 1. bid-response curve
function bidChart(r) {
  const W = 620, H = 250, L = 46, R = 14, T = 14, B = 34;
  const c = r.curve.filter(p => p.k >= 0.85 && p.k <= 1.3);
  const k0 = c[0].k, k1 = c.at(-1).k;
  const evMax = Math.max(...c.map(p => p.ev), 1e-9);
  const x = k => L + (k - k0) / (k1 - k0) * (W - L - R);
  const y = p => T + (1 - p) * (H - T - B);
  const path = f => c.map((p, i) => `${i ? "L" : "M"}${x(p.k).toFixed(1)},${f(p).toFixed(1)}`).join("");
  const ticks = [];
  for (let k = 0.85; k <= 1.3001; k += 0.05) ticks.push(k);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="เส้นโอกาสขายตาม k">
    ${ticks.map(k => `<line x1="${x(k)}" x2="${x(k)}" y1="${T}" y2="${H - B}" class="grid"/><text x="${x(k)}" y="${H - B + 16}" text-anchor="middle">${k.toFixed(2)}</text>`).join("")}
    <line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" class="axis"/>
    <text x="${L - 6}" y="${T + 4}" text-anchor="end">100%</text><text x="${L - 6}" y="${H - B}" text-anchor="end">0</text>
    <path d="${path(p => y(p.p))}" class="line-p"/><path d="${path(p => y(p.ev / evMax))}" class="line-ev"/>
    ${r.points.filter(p => p.k >= k0 && p.k <= k1).map((p, i) => `<circle cx="${x(p.k)}" cy="${y(p.won) + (p.won ? 6 : -6) + (i % 3) * (p.won ? 4 : -4)}" r="4.5" class="${p.won ? "dot-win" : "dot-lose"}"/>`).join("")}
    <line x1="${x(r.best.k)}" x2="${x(r.best.k)}" y1="${T}" y2="${H - B}" class="best"/>
    <text x="${x(r.best.k) + 4}" y="${T + 12}" class="best-label">k* ${r.best.k.toFixed(3)}</text>
  </svg><p class="legend"><span class="sw p"></span>P(ขายได้ | k) ที่ฟิต <span class="sw ev"></span>(k − V) × P(k)
    <span class="dot-key win"></span>ขายได้ <span class="dot-key lose"></span>ขายไม่ได้</p>`;
}
async function bid() {
  const out = $("bid-result");
  try {
    const r = await call({kind: "bidcurve", rows: rows(), ...fields("bid-form")});
    out.classList.remove("error");
    $("bid-pill").textContent = `k* ${k3(r.best.k)} · ${r.n} ซอง`;
    out.innerHTML = `<dl class="results">
      <dt>k* ที่ดีที่สุด</dt><dd><strong>${k3(r.best.k)}</strong></dd>
      <dt>ราคาที่พิมพ์ (ปัดลง)</dt><dd><strong>${r.price ? f2(r.price) + " บาท/กก." : "—"}</strong></dd>
      <dt>โอกาสขายที่ k*</dt><dd>${pc(r.best.p)}</dd>
      <dt>k ที่โอกาสขาย 50%</dt><dd>${k3(r.k50)}</dd>
      <dt>ความชัน (ยิ่งชันยิ่งแน่ใจ)</dt><dd>${r.b.toFixed(1)}</dd></dl>
      <p class="hint">${r.n < 4 ? "ข้อมูลยังน้อย ผลส่วนใหญ่มาจากความเชื่อตั้งต้น · " : ""}รวมทั้งเพดานและคู่แข่งไว้ในเส้นเดียว แยกตามผู้ซื้อหรือประเภทจะแม่นกว่า</p>`;
    $("bid-chart").innerHTML = bidChart(r);
  } catch (e) { fail(out, e); }
}

// ------------------------------------------------------------------ 2. inventory DP
async function dp() {
  const out = $("dp-result");
  try {
    const data = fields("dp-form");
    const r = await call({kind: "dp", ...data});
    out.classList.remove("error");
    $("dp-pill").textContent = `PR คาดหวัง ${r.expected_pr.toFixed(4)}`;
    const cols = [];
    const step = r.bags > 30 ? Math.ceil(r.bags / 30) : 1;
    for (let n = r.bags; n >= 1; n -= step) cols.push(n);
    const allK = r.policy.flat().filter(k => k != null);
    const kMin = Math.min(...allK), kMax = Math.max(...allK);
    const cell = (k, p) => k == null ? '<td class="heat none">—</td>' :
      `<td class="heat" style="--t:${((k - kMin) / (kMax - kMin || 1)).toFixed(3)}" title="โอกาสขายต่อกระสอบ ${pc(p)}">${k.toFixed(2)}</td>`;
    const policy = `<div class="table-scroll"><table class="heatmap"><thead><tr><th>รอบ ↓ / กระสอบที่เหลือ →</th>${cols.map(n => `<th>${n}</th>`).join("")}</tr></thead>
      <tbody>${r.policy.map((row, t) => `<tr><th>${t + 1}</th>${cols.map(n => cell(row[n], r.p[t][n])).join("")}</tr>`).join("")}</tbody></table></div>`;
    const marg = table(["รอบ", "ΔV ของกระสอบถัดไป (ใช้เป็น V)", "k* ที่สต็อกเต็ม", "มูลค่าทั้งหมดถ้าเริ่มรอบนี้ (÷ กระสอบ)"],
      r.policy.map((row, t) => [t + 1, `<strong>${k3(r.marginal[t][r.bags])}</strong>`, k3(row[r.bags]), k3(r.value[t][r.bags] / r.bags)]));
    out.innerHTML = `<h3 class="sub">k* ที่ดีที่สุดของแต่ละสถานะ</h3>${policy}
      <p class="hint">อ่านแบบนี้: ต้นเกมของยังเยอะ ราคาสูงได้ (ถือไว้ขายรอบหน้าได้) · ใกล้จบ k ลดลงหาขอบล่างเพดาน · ช่อง "—" = ไม่คุ้มขาย ถือไว้</p>
      <h3 class="sub">ค่าถือไว้ V ตามทฤษฎี</h3>${marg}
      <p class="hint">นำ ΔV ไปใส่ช่อง "ค่าถือไว้ V" ในหน้ากลยุทธ์ k* หรือใบประเมินมูลค่า · หลังรอบสุดท้าย ΔV = 0.80 (ขายซาก)</p>`;
  } catch (e) { fail(out, e); }
}

// ------------------------------------------------------------------ 3. bandit
async function bandit() {
  const out = $("bandit-result");
  try {
    const r = await call({kind: "bandit", rows: rows(), ...fields("bandit-form")});
    out.classList.remove("error");
    $("bandit-pill").textContent = `Thompson ${k3(r.thompson)} · UCB ${k3(r.ucb)}`;
    out.innerHTML = `<div class="metrics compact-metrics">
        <div class="metric accent"><small>Thompson sampling แนะนำ</small><strong>k ${k3(r.thompson)}</strong></div>
        <div class="metric"><small>UCB (มองโลกแง่ดี)</small><strong>k ${k3(r.ucb)}</strong></div>
        <div class="metric"><small>โลภ (ค่าเฉลี่ยดีสุด)</small><strong>k ${k3(r.greedy)}</strong></div>
        <div class="metric"><small>จำนวนครั้งที่สุ่ม</small><strong>${f0(r.draws)}</strong></div></div>` +
      table(["k", "ลอง", "ขายได้", "โอกาสขาย (เฉลี่ยหลังเรียนรู้)", "รางวัลเฉลี่ย (k − V)·P", "UCB", "P(ดีที่สุด)"], r.arms.map(a => [
        `<strong>${k3(a.k)}</strong>`, a.trials, a.wins, pc(a.p_mean), a.reward_mean.toFixed(4), a.ucb.toFixed(4),
        `<span class="bar" style="--w:${(100 * a.p_best).toFixed(1)}%"></span> ${pc(a.p_best)}`])) +
      `<p class="hint">ใช้ Thompson เมื่อยังเหลือหลายรอบ (สำรวจพอดีๆ) · ใช้แบบโลภในรอบสุดท้าย (ไม่มีเวลาใช้ความรู้ใหม่แล้ว)</p>`;
  } catch (e) { fail(out, e); }
}

// ------------------------------------------------------------------ score lever (pure JS)
function score() {
  const d = fields("score-form");
  const pr = num(d.pr) / 100, lead = num(d.leader) / 100, total = num(d.total), mape = num(d.mape) / 100;
  const tv = num(d.tv), k = num(d.k) / 100, bags = num(d.bags);
  const best = Math.max(pr, lead);
  const sell = best > 0.8 ? 80 * Math.max(0, pr - 0.8) / (best - 0.8) : 0;
  const appr = 20 * Math.max(0, 1 - mape / 0.10);
  const dPr = (k - 0.8) * tv * 60 * bags / total;
  const dPts = best > 0.8 ? 80 * dPr / (best - 0.8) : 0;
  const perPoint = best > 0.8 ? 80 * 0.01 / (best - 0.8) : 0;
  $("score-result").innerHTML = `<div class="metrics compact-metrics">
      <div class="metric accent"><small>คะแนนรวมโดยประมาณ</small><strong>${(sell + appr).toFixed(1)} / 100</strong></div>
      <div class="metric"><small>คะแนนขาย / 80</small><strong>${sell.toFixed(1)}</strong></div>
      <div class="metric"><small>คะแนนประเมิน / 20</small><strong>${appr.toFixed(1)}</strong></div>
      <div class="metric"><small>PR +0.01 = กี่คะแนน</small><strong>+${perPoint.toFixed(1)}</strong></div></div>
    <p>ขาย ${bags} กระสอบ ที่ k ${k.toFixed(3)} (TV ${f2(tv)}) เทียบปล่อยเป็นซาก: PR <strong>+${(100 * dPr).toFixed(3)} จุด %</strong>
      ≈ <strong>+${dPts.toFixed(2)} คะแนน</strong> · ถ้าเราเป็นผู้นำ คะแนนขายเต็ม 80 อยู่แล้ว การขายเพิ่มจะดันคนอื่นลงแทน</p>`;
}

// ------------------------------------------------------------------ wiring
$("from-r1").addEventListener("click", () => {
  const g = restore("tesa-r1", null);
  if (!g) return;
  const tv = Object.fromEntries((g.lots || []).map(l => [l.lot_id, valuation(num(l.moisture_pct), num(l.defect_pct), num(l.screen))?.tv]));
  const lines = (g.rounds || []).flatMap(r => r.results || []).filter(o => o.status && tv[o.lot_id] && o.price_per_kg)
    .map(o => `${(Number(o.price_per_kg) / tv[o.lot_id]).toFixed(4)} ${o.status}`);
  if (lines.length) {
    $("history").value = lines.join("\n");
    save("tesa-research-history", $("history").value);
    bid();
    bandit();
  } else {
    const n = $("notice");
    n.hidden = false;
    n.classList.add("bad");
    n.textContent = "ยังไม่มีผลในหน้าแข่งรอบ 1";
  }
});
$("clear-history").addEventListener("click", () => { $("history").value = ""; bid(); bandit(); });
const saved = restore("tesa-research-history", null);
if (saved) $("history").value = saved;
$("history").addEventListener("input", debounce(() => { save("tesa-research-history", $("history").value); bid(); bandit(); }));
$("bid-form").addEventListener("input", debounce(bid));
$("dp-form").addEventListener("input", debounce(dp, 400));
$("bandit-form").addEventListener("input", debounce(bandit));
$("score-form").addEventListener("input", score);
bid();
dp();
bandit();
score();
