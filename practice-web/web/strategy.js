// Strategy tab: k* finder, round planner, Monte-Carlo simulator. Offline maths only (local server).
const $ = id => document.getElementById(id);
const n2 = new Intl.NumberFormat("th-TH", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const n0 = new Intl.NumberFormat("th-TH", {maximumFractionDigits: 0});
const f2 = v => v == null ? "—" : n2.format(v);
const k3 = v => v == null ? "—" : Number(v).toFixed(3);
const pc = v => v == null ? "—" : `${(100 * v).toFixed(1)}%`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

async function call(data) {
  const r = await fetch("/api/strategy", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || "คำนวณไม่สำเร็จ");
  return body;
}
function table(headers, rows) {
  return `<div class="table-scroll"><table><thead><tr>${headers.map(h => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function dl(items) {
  return `<dl class="results">${items.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join("")}</dl>`;
}
function store(key, value) {
  try { value === undefined ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { /* storage blocked */ }
}
function load(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function formData(form) {
  const data = Object.fromEntries(new FormData(form));
  for (const box of form.querySelectorAll("input[type=checkbox]")) data[box.name] = box.checked ? "1" : "0";
  return data;
}

// ------------------------------------------------------------------ k* chart (inline SVG)
function chart(curve, best, lo, hi, reserve) {
  if (!curve.length) return "";
  const W = 560, H = 230, L = 44, R = 14, T = 14, B = 34;
  const k0 = curve[0].k, k1 = curve.at(-1).k;
  const evMax = Math.max(...curve.map(c => c.ev), 1e-9);
  const x = k => L + (k - k0) / (k1 - k0 || 1) * (W - L - R);
  const yP = p => T + (1 - p) * (H - T - B);
  const yE = e => T + (1 - e / evMax) * (H - T - B);
  const path = (fn) => curve.map((c, i) => `${i ? "L" : "M"}${x(c.k).toFixed(1)},${fn(c).toFixed(1)}`).join("");
  const ticks = [];
  for (let k = Math.ceil(k0 * 20) / 20; k <= k1 + 1e-9; k += 0.05) ticks.push(k);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="กราฟโอกาสขายและมูลค่าคาดหวังตาม k">
    <rect x="${x(Math.max(lo, k0))}" y="${T}" width="${Math.max(0, x(Math.min(hi, k1)) - x(Math.max(lo, k0)))}" height="${H - T - B}" class="band"/>
    ${ticks.map(k => `<line x1="${x(k)}" x2="${x(k)}" y1="${T}" y2="${H - B}" class="grid"/><text x="${x(k)}" y="${H - B + 16}" text-anchor="middle">${k.toFixed(2)}</text>`).join("")}
    <line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" class="axis"/>
    <text x="${L - 6}" y="${T + 4}" text-anchor="end">100%</text><text x="${L - 6}" y="${H - B}" text-anchor="end">0</text>
    <path d="${path(c => yP(c.p))}" class="line-p"/>
    <path d="${path(c => yE(c.ev))}" class="line-ev"/>
    <line x1="${x(best)}" x2="${x(best)}" y1="${T}" y2="${H - B}" class="best"/>
    <text x="${x(best) + 4}" y="${T + 12}" class="best-label">k* ${best.toFixed(3)}</text>
    <text x="${W / 2}" y="${H - 2}" text-anchor="middle">k = ราคา ÷ TV · แถบ = ช่วงเพดานที่เชื่อ · V = ${reserve.toFixed(2)}</text>
  </svg>
  <p class="legend"><span class="sw p"></span>โอกาสขายได้ P(k) <span class="sw ev"></span>มูลค่าคาดหวัง (k − V) × P(k)</p>`;
}

$("kstar").addEventListener("submit", async event => {
  event.preventDefault();
  const out = $("kstar-result");
  try {
    const r = await call({kind: "kstar", ...formData(event.target)});
    out.classList.remove("error");
    out.innerHTML = dl([
      ["k* ที่ดีที่สุด", `<strong>${k3(r.k)}</strong>`],
      ["ราคาที่พิมพ์ (ปัดลง)", `<strong>${f2(r.price)} บาท/กก.</strong>`],
      ["โอกาสขายได้ที่ k*", pc(r.p)],
      ["มูลค่าคาดหวังเหนือ V (ทั้งหมด)", `${n0.format(r.ev_thb)} บาท`],
      ["ช่วงเพดานที่เชื่อ", `${k3(r.lo)} – ${k3(r.hi)}`],
      ["ขายชัวร์ที่ขอบล่าง", `k ${k3(r.sure_k)} → ${n0.format(r.sure_ev_thb)} บาท`],
      ["k ตัดของคู่แข่ง", r.clear_k ? k3(r.clear_k) : "ไม่มี (ติดเพดาน)"],
    ]) + `<p class="hint">ถ้าแบ่งเป็นขั้นบันได: ${r.ladder.map(s => `${s.bags} กระสอบ @ ${f2(s.price)} (k ${k3(s.k)})`).join(" · ")}</p>`;
    $("kstar-chart").innerHTML = chart(r.curve, r.k, r.lo, r.hi, r.reserve);
  } catch (e) {
    out.classList.add("error");
    out.textContent = e.message;
    $("kstar-chart").innerHTML = "";
  }
});

// ------------------------------------------------------------------ round planner
const TEMPLATE = `ROUNDS 6
LOT A 5 1.79 13.23 14
LOT B 6 3.40 11.59 16
LOT C 7 5.56 12.95 13
LOT D 8 6.02 14.05 16

ROUND 1
RFQ R1-01 COM 9 สยามคอมเมิร์ซ
RFQ R1-02 INS 6 ทรีอินวันฟู้ดส์
RFQ R1-03 COM 13 ชุมพรคั่วบด
RFQ R1-04 INS 5 อินสแตนท์ไทย
`;
const area = $("plan").elements.text;
area.value = load("tesa-plan-text") || TEMPLATE;
area.addEventListener("input", () => store("tesa-plan-text", area.value));

$("plan").addEventListener("submit", async event => {
  event.preventDefault();
  const out = $("plan-result");
  try {
    const r = await call({kind: "plan", ...formData(event.target)});
    out.classList.remove("error");
    const offers = r.offers.length ? table(["RFQ", "ผู้ซื้อ", "ล็อต", "กระสอบ", "ราคาพิมพ์", "k", "P", "EV บาท", ""],
      r.offers.map(o => [esc(o.rfq_id), `${esc(o.buyer)}<small> ${esc(o.buyer_type)}</small>`, esc(o.lot_id), o.bags,
        `<strong>${f2(o.price)}</strong>`, k3(o.k), pc(o.p), n0.format(o.ev),
        o.kind === "probe" ? '<span class="pill warn">ทดลอง</span>' : '<span class="pill good">หลัก</span>'])) :
      '<p class="hint">ยังไม่มีซองแนะนำ (เพิ่ม ROUND ใหม่พร้อม RFQ)</p>';
    out.innerHTML = offers + (r.errors.length ? `<p class="negative">${r.errors.map(esc).join("<br>")}</p>` : "") +
      `<details><summary>รายละเอียดทั้งหมด (TV ที่ส่งประเมิน, หลักฐานเพดาน, V)</summary><pre class="code">${esc(r.report)}</pre></details>`;
  } catch (e) {
    out.classList.add("error");
    out.textContent = e.message;
  }
});

// ------------------------------------------------------------------ simulator
function heat(r) {
  const all = r.cells.flat().map(c => c.pr);
  const lo = Math.min(...all), hi = Math.max(...all);
  const cell = c => {
    const t = hi > lo ? (c.pr - lo) / (hi - lo) : 1;
    return `<td class="heat" style="--t:${t.toFixed(3)}" title="PR ${c.pr.toFixed(4)} · ${c.points.toFixed(1)} คะแนน">${c.pr.toFixed(3)}</td>`;
  };
  return `<p><strong>ดีที่สุด:</strong> ${r.types[0]} k ${r.best.a.toFixed(2)} · ${r.types[1]} k ${r.best.b.toFixed(2)} → PR ${r.best.pr.toFixed(4)} (${r.best.points.toFixed(1)} คะแนน)</p>
    <div class="table-scroll"><table class="heatmap"><thead><tr><th>${r.types[0]} ↓ / ${r.types[1]} →</th>${r.b.map(b => `<th>${b.toFixed(2)}</th>`).join("")}</tr></thead>
    <tbody>${r.cells.map((row, i) => `<tr><th>${r.a[i].toFixed(2)}</th>${row.map(cell).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="hint">k คงที่ทั้งเกมเป็นเส้นฐาน planner ที่เรียนรู้ระหว่างเกมควรชนะค่านี้ ถ้าไม่ชนะ ให้ปรับค่าตั้งต้นใน pricing.PRIORS</p>`;
}

$("simulate").addEventListener("submit", async event => {
  event.preventDefault();
  const out = $("simulate-result");
  const kind = event.submitter?.value || "simulate";
  const data = {kind, ...formData(event.target)};
  if (kind === "grid") data.games = Math.min(Number(data.games) || 8, 10);
  out.classList.remove("error");
  out.innerHTML = '<p class="hint">กำลังจำลอง… (ตารางใช้เวลาราว 10–60 วินาที)</p>';
  event.target.querySelectorAll("button").forEach(b => b.disabled = true);
  try {
    const r = await call(data);
    if (kind === "grid") {
      out.innerHTML = heat(r);
    } else {
      out.innerHTML = `<p class="hint">${r.teams} ทีม · ${r.rounds} รอบ · ใช้เวลา ${r.seconds} วินาที · คะแนนขายเทียบทีมที่ดีที่สุดในแต่ละเกม</p>` +
        table(["กลยุทธ์", "PR เฉลี่ย", "± SD", "คะแนนขาย /80", "อันดับเฉลี่ย", "ชนะ", "ขายได้"], r.rows.map(x => [
          esc(x.name), x.pr_mean.toFixed(4), x.pr_sd.toFixed(3), `<strong>${x.points_mean.toFixed(1)}</strong>`,
          x.rank_mean.toFixed(1), pc(x.win_rate), pc(x.sold_share)])) +
        `<details><summary>ดูเกมตัวอย่างของ planner รอบต่อรอบ (PR ${r.sample_pr.toFixed(4)} / ผู้นำ ${r.sample_best.toFixed(4)})</summary>${
          r.sample_log.map(rnd => `<h3>รอบ ${rnd.round}</h3>` + table(["RFQ", "ประเภท", "ซื้อ/ขอ", "ซอง", "เพดานจริง"],
            rnd.rfqs.map(s => [s.rfq_id, s.buyer_type, `${s.bought_kg / 60}/${s.wanted_kg / 60}`, s.offers, k3(s.ceiling)])) +
            (rnd.ours.length ? table(["ซองเรา", "ล็อต", "กระสอบ", "ราคา", "ผล"], rnd.ours.map(o => [o.rfq_id, o.lot_id,
              `${o.filled_kg / 60}/${o.kg / 60}`, f2(o.price_per_kg), o.status])) : '<p class="hint">ไม่มีซอง</p>')).join("")}</details>`;
    }
  } catch (e) {
    out.classList.add("error");
    out.textContent = e.message;
  } finally {
    event.target.querySelectorAll("button").forEach(b => b.disabled = false);
  }
});
