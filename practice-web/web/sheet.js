// "ใบประเมินมูลค่า" sheet: one lot or many lots at once, live TV, appraisal list, k* and the best-PR table.
import {$, call, esc, f0, f2, f4, k3, num, pc, restore, save, table, typePill, valuation} from "./common.js";

const KEY = "tesa-sheet";
const EXAMPLE = [
  {lot_id: "LOT-T05-1", moisture: 12.73, defect: 1.62, screen: 14, bags: 3},
  {lot_id: "LOT-T05-2", moisture: 11.88, defect: 3.12, screen: 14, bags: 6},
  {lot_id: "LOT-T05-3", moisture: 11.01, defect: 7.57, screen: 16, bags: 5},
  {lot_id: "LOT-T05-4", moisture: 14.05, defect: 6.02, screen: 16, bags: 7},
];
const blank = i => ({lot_id: `LOT-${i + 1}`, moisture: "", defect: "", screen: "", bags: ""});
let rows = restore(KEY, null) || [0, 1, 2, 3].map(blank);
let selected = 0;
let timer = null;

const FIELDS = [["lot_id", "A ล็อต", "text"], ["moisture", "B ชื้น %", "number"], ["defect", "C เสีย %", "number"],
  ["screen", "D ขนาด", "number"], ["bags", "E กระสอบ", "number"]];

function calc(r) {
  return valuation(num(r.moisture), num(r.defect), num(r.screen));
}
function computed(r) {
  const v = calc(r);
  const bags = num(r.bags);
  if (!v) return ["", "", "", "", "", "", ""].map(x => `<td class="calc">${x}</td>`).join("");
  return `<td class="calc">${v.grade}</td><td class="calc">${f4(v.g)}</td><td class="calc">${f4(v.m)}</td><td class="calc">${v.s.toFixed(2)}</td>
    <td class="calc tv"><strong>${f2(v.tv2)}</strong></td><td class="calc">${f2(0.8 * v.tv)}</td>
    <td class="calc">${Number.isFinite(bags) && bags > 0 ? f0(v.tv * bags * 60) : ""}</td>`;
}
function render() {
  $("sheet").innerHTML = `<thead><tr>${FIELDS.map(f => `<th>${f[1]}</th>`).join("")}<th>F เกรด</th><th>H G</th><th>I M</th><th>J S</th>
    <th>K TV</th><th>L 0.8×TV</th><th>M รวม (บาท)</th></tr></thead><tbody>${rows.map((r, i) => `<tr data-row="${i}" class="${i === selected ? "selected" : ""}">${
      FIELDS.map(([name, label, type]) => `<td class="in"><input data-field="${name}" type="${type}" ${type === "number" ? 'step="any" min="0"' : ""}
        value="${esc(r[name])}" aria-label="${label} แถว ${i + 1}"></td>`).join("")}${computed(r)}</tr>`).join("")}</tbody>`;
  detail();
  update();
}
function detail() {
  const r = rows[selected];
  const v = r && calc(r);
  if (!v) {
    $("sheet-detail").innerHTML = "";
    return;
  }
  const d = num(r.defect), m = num(r.moisture);
  const mText = m <= 12.5 ? `${m} ≤ 12.5 → ไม่ถูกหัก M = 1` :
    m <= 15 ? `(1 − ${m}/100) ÷ 0.875 − 0.02 × (${m} − 12.5) = ${f4((1 - m / 100) / 0.875)} − ${f4(0.02 * (m - 12.5))} = ${f4(v.m)}` :
      `${m} > 15 → M = 0.60`;
  $("sheet-detail").innerHTML = `<div class="detail-card"><h3>${esc(r.lot_id)} · วิธีคิด</h3>
    <div class="steps">
      <div><b>G</b> ${d} ≤ ${v.limit === Infinity ? "∞" : v.limit} → ${v.grade} ตัวคูณ ${v.step.toFixed(2)}<br>(1 − ${d}/100) × ${v.step.toFixed(2)} = <strong>${f4(v.g)}</strong></div>
      <div><b>M</b> ${mText}</div>
      <div><b>S</b> ตะแกรง ${num(r.screen)}${v.screen !== num(r.screen) ? ` (ใช้ ${v.screen})` : ""} → <strong>${v.s.toFixed(2)}</strong></div>
      <div><b>TV</b> 120 × ${f4(v.g)} × ${f4(v.m)} × ${v.s.toFixed(2)} = <strong>${v.tv.toFixed(6)}</strong> → ส่ง <strong>${f2(v.tv2)}</strong></div>
      <div><b>0.80 × TV</b> = ${f2(0.8 * v.tv)} · ราคาต่ำสุดที่ยังได้กำไร ${f2(Math.floor(0.8 * v.tv * 100) / 100 + 0.01)} บาท/กก.</div>
    </div></div>`;
}
function complete() {
  return rows.length && rows.every(r => calc(r) && num(r.bags) > 0);
}
function settings() {
  const data = {evidence: {}};
  for (const input of $("sheet-settings").querySelectorAll("input")) {
    const value = input.type === "checkbox" ? (input.checked ? "1" : "0") : input.value;
    if (input.name.includes(".")) {
      const [t, k] = input.name.split(".");
      if (value !== "") (data.evidence[t] ||= {})[k] = value;
    } else data[input.name] = value;
  }
  return data;
}
function appraisalCard() {
  const ready = rows.filter(r => calc(r));
  $("sheet-appraisal").innerHTML = ready.length ? table(["ล็อต", "ผลวัดตัวอย่าง", "มูลค่าที่ประเมิน (บาท/กก.)"], ready.map(r => [
    `<strong>${esc(r.lot_id)}</strong>`, `ความชื้น ${r.moisture}% · เมล็ดเสีย ${r.defect}% · ขนาด ${r.screen}`,
    `<span class="typed">${f2(calc(r).tv2)}</span>`]), "appraise") +
    `<div class="actions"><button type="button" class="secondary" data-sheet="copy">คัดลอกค่า TV ทั้งหมด</button></div>` : '<p class="hint">กรอกล็อตให้ครบก่อน</p>';
}
function update() {
  appraisalCard();
  clearTimeout(timer);
  if (!complete()) {
    $("sheet-plan").innerHTML = '<p class="hint">กรอกทุกล็อตให้ครบ (ชื้น เสีย ขนาด กระสอบ) ตารางจะขึ้นเอง</p>';
    $("sheet-pr").textContent = "";
    return;
  }
  timer = setTimeout(async () => {
    try {
      const r = await call({kind: "sheet", lots: rows, ...settings()});
      plan(r);
    } catch (e) {
      $("sheet-plan").innerHTML = `<p class="negative">${esc(e.message)}</p>`;
    }
  }, 250);
}
function plan(r) {
  const tvOf = Object.fromEntries(r.lots.map(l => [l.lot_id, l]));
  const body = r.plan.map(row => {
    const lot = tvOf[row.lot_id];
    const b = row.best;
    const others = row.options.filter(o => o.buyer_type !== b?.buyer_type).map(o => o.eligible ?
      `${typePill(o.buyer_type)} k ${k3(o.k)} · ${f2(o.price)}` : `${typePill(o.buyer_type)} <span class="muted">ไม่ผ่านสเปก</span>`).join("<br>");
    return b ? [`<strong>${esc(row.lot_id)}</strong><small>${lot.grade}</small>`, f2(lot.tv_2dp), lot.bags, typePill(b.buyer_type),
      `<strong>${k3(b.k)}</strong><small>เพดาน ${k3(b.lo)}–${k3(b.hi)}</small>`, `<span class="typed">${f2(b.price)}</span>`, pc(b.p),
      f0(b.revenue_if_sold), `+${(100 * b.delta_pr).toFixed(2)}`, `≤ ${f2(b.max_price)}`, others] :
      [`<strong>${esc(row.lot_id)}</strong><small>${lot.grade}</small>`, f2(lot.tv_2dp), lot.bags, '<span class="result bad">ไม่ผ่านสเปกผู้ซื้อใด</span>',
        "0.80", "ปล่อยไว้", "—", f0(0.8 * lot.value), "0.00", "—", others];
  });
  const pts = r.points_expected == null ? "" : ` · คะแนนขาย ≈ ${r.points_expected.toFixed(1)} / 80`;
  $("sheet-pr").textContent = `PR คาดหวัง ${r.pr_expected.toFixed(4)}${pts}`;
  $("sheet-plan").innerHTML = table(["ล็อต", "TV", "กระสอบ", "ผู้ซื้อที่ดีที่สุด", "k*", "ราคาพิมพ์ (บาท/กก.)", "โอกาสขาย",
    "รายได้ถ้าขายหมด", "ΔPR (จุด %)", "ไม่ควรเกิน", "ทางเลือกอื่น"], body, "plan") +
    `<div class="metrics compact-metrics">
      <div class="metric"><small>มูลค่า TV รวม</small><strong>${f0(r.total_value)} ฿</strong></div>
      <div class="metric accent"><small>PR คาดหวัง (ตามโอกาสขาย)</small><strong>${r.pr_expected.toFixed(4)}</strong></div>
      <div class="metric"><small>PR ถ้าขายหมดที่ k*</small><strong>${r.pr_all_sold.toFixed(4)}</strong></div>
      <div class="metric"><small>ไม่ขายเลย</small><strong>0.8000</strong></div></div>
    <p class="hint">ΔPR = (k − 0.80) × TV × กก. ÷ มูลค่ารวม · ล็อต TV สูงได้ PR ต่อจุด k มากกว่า จึงควรได้ RFQ ที่ดีกว่าก่อน · "ไม่ควรเกิน" = ขอบบนเพดานที่เชื่อ</p>`;
}

$("sheet").addEventListener("input", e => {
  const tr = e.target.closest("tr[data-row]");
  if (!tr) return;
  const i = Number(tr.dataset.row);
  rows[i][e.target.dataset.field] = e.target.value;
  save(KEY, rows);
  tr.querySelectorAll("td.calc").forEach(td => td.remove());
  tr.insertAdjacentHTML("beforeend", computed(rows[i]));
  selected = i;
  detail();
  update();
});
$("sheet").addEventListener("focusin", e => {
  const tr = e.target.closest("tr[data-row]");
  if (!tr || Number(tr.dataset.row) === selected) return;
  selected = Number(tr.dataset.row);
  document.querySelectorAll("#sheet tr.selected").forEach(x => x.classList.remove("selected"));
  tr.classList.add("selected");
  detail();
});
$("sheet-settings").addEventListener("input", update);
document.getElementById("tools").addEventListener("click", async e => {
  const action = e.target.closest("[data-sheet]")?.dataset.sheet;
  if (!action) return;
  if (action === "add" && rows.length < 12) rows.push(blank(rows.length));
  if (action === "remove" && rows.length > 1) rows.pop();
  if (action === "example") rows = EXAMPLE.map(r => ({...r}));
  if (action === "clear" && confirm("ล้างทุกแถว?")) rows = [0, 1, 2, 3].map(blank);
  if (action === "copy") {
    const text = rows.filter(calc).map(r => `${r.lot_id}\t${calc(r).tv2.toFixed(2)}`).join("\n");
    try { await navigator.clipboard.writeText(text); e.target.textContent = "คัดลอกแล้ว"; } catch { /* clipboard blocked */ }
    return;
  }
  selected = Math.min(selected, rows.length - 1);
  save(KEY, rows);
  render();
});
render();
