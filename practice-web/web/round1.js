// Live manual cockpit (any competition day): what /trade shows -> recommended offers to TYPE BY HAND.
// Strategy controls: hold the round, per-lot hold/exact/max bags + min k, per-RFQ skip / your price / max bags /
// competitor price, bag range, PR target, risk, ladder, random undercut, capacity, spec margins.
// Nothing here talks to the real market: it only calls the local /api/strategy (planner maths).
import {$, call, esc, f0, f2, k3, normType, num, pc, restore, save, SPECS, specText, STATUS, statusText, table, typePill, valuation}
  from "./common.js";

const KEY = "tesa-r1";
const fresh = () => ({rounds_total: 6, festival: "", floor: 85, patience: 0.6,
  lots: [1, 2, 3, 4].map(i => ({lot_id: `LOT-${i}`, bags: "", moisture_pct: "", defect_pct: "", screen: ""})),
  rounds: [newRound(1)], view: 0});
function newRound(number) {
  return {number, rfqs: [], results: [], market: [], plan: null};
}
let g = restore(KEY, null) || fresh();
g.ctrl ||= {sell_round: true, risk: "balanced", seed: 18};
let last = null;          // last server answer
let clock = {left: 480, running: false, id: null};

const cur = () => g.rounds[g.view];
const persist = () => save(KEY, g);

// ------------------------------------------------------------------ lots
function lotsTable() {
  const head = ["ล็อต", "กระสอบ (จาก)", "ชื้น %", "เสีย %", "ขนาด", "เกรด", "TV", "เหลือ", "รอบนี้", "กระสอบ", "k ขั้นต่ำ %", ""];
  const stock = Object.fromEntries((last?.stock || []).map(s => [s.lot_id, s]));
  $("r1-lots").innerHTML = `<thead><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${g.lots.map((l, i) => {
    const v = valuation(num(l.moisture_pct), num(l.defect_pct), num(l.screen));
    const st = stock[l.lot_id];
    return `<tr data-lot="${i}">${["lot_id", "bags", "moisture_pct", "defect_pct", "screen"].map(f =>
      `<td class="in"><input data-f="${f}" type="${f === "lot_id" ? "text" : "number"}" step="any" value="${esc(l[f])}" aria-label="${f} ล็อต ${i + 1}"></td>`).join("")}
      <td class="calc">${v ? v.grade : ""}</td><td class="calc tv"><strong>${v ? f2(v.tv2) : ""}</strong></td>
      <td class="calc">${st ? `${st.left} / ${st.total}` : ""}</td>
      <td class="rule"><select data-rule="mode" aria-label="รอบนี้ ล็อต ${i + 1}">${[["auto", "อัตโนมัติ"], ["hold", "ไม่ขาย"],
        ["exact", "ขายพอดี N"], ["max", "ไม่เกิน N"]].map(([v, t]) => `<option value="${v}" ${(l.mode || "auto") === v ? "selected" : ""}>${t}</option>`).join("")}</select></td>
      <td class="rule"><input data-rule="rule_bags" type="number" min="0" value="${esc(l.rule_bags ?? "")}" placeholder="N" ${["exact", "max"].includes(l.mode) ? "" : "disabled"}></td>
      <td class="rule"><input data-rule="min_k" type="number" step="any" value="${esc(l.min_k ?? "")}" placeholder="เช่น 100"></td>
      <td><button type="button" class="link" data-dellot="${i}" aria-label="ลบล็อต">×</button></td></tr>`;
  }).join("")}</tbody>`;
}

// ------------------------------------------------------------------ RFQs (slide 22 style rows)
function rfqRows() {
  const r = cur();
  $("r1-rfq-title").textContent = `คำขอซื้อ (RFQ) รอบ ${r.number}`;
  const recs = r.plan?.offers || [];
  if (!r.rfqs.length) {
    $("r1-rfqs").innerHTML = '<p class="hint">ยังไม่มี RFQ · กด "+ RFQ" ทีละใบตามหน้า /trade</p>';
    return;
  }
  $("r1-rfqs").innerHTML = r.rfqs.map((q, i) => {
    const t = normType(q.buyer_type);
    const spec = q.spec || SPECS[t];
    const mine = recs.filter(o => o.rfq_id === q.rfq_id);
    const lotsOk = last?.eligible?.[q.rfq_id];
    return `<div class="rfq-row ${mine.length ? "offered" : ""}" data-rfq="${i}">
      <div class="rfq-main">
        <div class="rfq-line"><input class="inline id" data-q="rfq_id" value="${esc(q.rfq_id)}" aria-label="รหัส RFQ">
          <input class="inline name" data-q="buyer" value="${esc(q.buyer)}" placeholder="ชื่อผู้ซื้อ" aria-label="ชื่อผู้ซื้อ">
          <select class="inline" data-q="buyer_type" aria-label="ประเภทผู้ซื้อ">${["COMMERCIAL", "INSTANT", "SPECIALTY"].map(x =>
            `<option value="${x}" ${x === t ? "selected" : ""}>${x[0] + x.slice(1).toLowerCase()}</option>`).join("")}</select>
          ต้องการ <input class="inline bags" data-q="bags" type="number" min="1" value="${esc(q.bags)}" aria-label="กระสอบที่ต้องการ"> กระสอบ</div>
        <div class="rfq-spec">${t === "SPECIALTY" ? `เสีย ≤<input class="inline tiny" data-s="max_defect_pct" type="number" step="any" value="${esc(spec.max_defect_pct)}">%
          · ชื้น ≤<input class="inline tiny" data-s="max_moisture_pct" type="number" step="any" value="${esc(spec.max_moisture_pct)}">%
          · ตะแกรง ≥<input class="inline tiny" data-s="min_screen" type="number" value="${esc(spec.min_screen ?? "")}">` : specText(spec)}
          ${lotsOk ? ` · ล็อตที่ผ่าน: ${lotsOk.length ? lotsOk.map(esc).join(", ") : "ไม่มี"}` : ""}</div>
        <div class="rfq-rules">
          <label class="check"><input type="checkbox" data-r="skip" ${q.rule?.skip ? "checked" : ""}> ข้าม</label>
          <label>ราคาเรา<input data-r="price" type="number" step="0.01" value="${esc(q.rule?.price ?? "")}" placeholder="บาท/กก."></label>
          <label>หรือ k %<input data-r="k" type="number" step="any" value="${esc(q.rule?.k ?? "")}" placeholder="เช่น 103"></label>
          <label>ไม่เกิน<input data-r="max_bags" type="number" min="0" value="${esc(q.rule?.max_bags ?? "")}" placeholder="กระสอบ"></label>
          <label>คู่แข่งต่ำสุด<input data-r="comp_price" type="number" step="0.01" value="${esc(q.rule?.comp_price ?? "")}" placeholder="บาท/กก."></label>
          <label>หรือ k %<input data-r="comp_k" type="number" step="any" value="${esc(q.rule?.comp_k ?? "")}" placeholder="เช่น 101"></label>
          ${last?.comp_k?.[q.rfq_id] ? `<small>≈ k ${k3(last.comp_k[q.rfq_id])}</small>` : ""}
        </div>
        ${mine.map(o => `<div class="rfq-rec">✓ แนะนำ <strong>${esc(o.lot_id)} · ${o.bags} กระสอบ @ ${f2(o.price)}</strong>
          <small>k ${k3(o.k)} · โอกาส ${pc(o.p)}${o.kind === "probe" ? " · ซองทดลอง" : ""}</small></div>`).join("")}
      </div>
      <button type="button" class="link" data-delrfq="${i}" aria-label="ลบ RFQ">×</button>
    </div>`;
  }).join("");
}

// ------------------------------------------------------------------ plan table
function planTable() {
  const r = cur();
  $("r1-round-pill").textContent = `รอบ ${r.number} / ${g.rounds_total}`;
  const p = r.plan;
  if (!p) {
    $("r1-plan").innerHTML = '<p class="hint">ใส่ RFQ แล้วกด "คำนวณราคาแนะนำ"</p>';
    return;
  }
  const rows = p.offers.map((o, i) => [`<label class="check"><input type="checkbox" data-typed="${i}" ${o.typed ? "checked" : ""}> ยื่นแล้ว</label>`,
    esc(o.rfq_id), `${esc(o.buyer)} ${typePill(o.buyer_type)}`, `<strong>${esc(o.lot_id)}</strong>`, `<strong>${o.bags}</strong>`,
    `<span class="typed">${f2(o.price)}</span>`, k3(o.k), pc(o.p), f0(o.ev),
    ({probe: '<span class="pill warn">ทดลอง 1 กระสอบ</span>', edge: '<span class="pill warn">ชิดเพดาน</span>',
      fill: '<span class="pill">เติมตามขั้นต่ำ</span>'})[o.kind] || (o.note === "your price" ? '<span class="pill">ราคาคุณ</span>' : '<span class="pill good">หลัก</span>')]);
  $("r1-plan").innerHTML = (rows.length ? table(["", "RFQ", "ผู้ซื้อ", "ล็อต", "กระสอบ", "ราคา (บาท/กก.)", "k", "โอกาสขาย", "EV เหนือ V (บาท)", ""], rows, "plan") :
    '<p class="hint">ไม่มีซองที่คุ้มกว่าถือไว้ในรอบนี้</p>') +
    `<p class="hint">${(p.notes || []).map(esc).join(" · ")}</p>` +
    (Object.keys(p.reserve || {}).length ? `<p class="hint">ค่าถือไว้ V: ${Object.entries(p.reserve).map(([k, v]) => `${esc(k)} ${k3(v)}`).join(" · ")}
      (รอบสุดท้าย V = 0.80 → ขายทุกอย่างที่ผ่านสเปก)</p>` : "") +
    ((p.unsellable || []).length ? `<p class="negative">ไม่ผ่านสเปกผู้ซื้อใดเลย: ${p.unsellable.map(esc).join(", ")} → ปล่อยไว้ ระบบขายให้ 0.80 × TV</p>` : "");
}

// ------------------------------------------------------------------ results + market summary (slide 20)
function resultsTable() {
  const r = cur();
  const rfqOpts = sel => r.rfqs.map(q => `<option ${q.rfq_id === sel ? "selected" : ""}>${esc(q.rfq_id)}</option>`).join("");
  const lotOpts = sel => g.lots.map(l => `<option ${l.lot_id === sel ? "selected" : ""}>${esc(l.lot_id)}</option>`).join("");
  $("r1-results").innerHTML = r.results.length ? table(["คำขอซื้อ (RFQ)", "ล็อต", "ราคา บาท/กก.", "ผล", "ขายได้ (กระสอบ)", ""], r.results.map((o, i) => [
    `<select data-res="${i}" data-k="rfq_id">${rfqOpts(o.rfq_id)}</select>`,
    `<select data-res="${i}" data-k="lot_id">${lotOpts(o.lot_id)}</select>`,
    `<input data-res="${i}" data-k="price_per_kg" type="number" step="0.01" value="${esc(o.price_per_kg)}">`,
    `<select data-res="${i}" data-k="status"><option value="">— เลือกผล —</option>${Object.keys(STATUS).filter(s => s !== "CANCELLED").map(s =>
      `<option value="${s}" ${s === o.status ? "selected" : ""}>${s} · ${STATUS[s][0]}</option>`).join("")}</select>`,
    `<input data-res="${i}" data-k="filled_bags" type="number" min="0" value="${esc(o.filled_bags)}">`,
    `<button type="button" class="link" data-delres="${i}">×</button>`]), "entry") : '<p class="hint">ยังไม่มีผล</p>';
  const byId = Object.fromEntries(r.market.map(m => [m.rfq_id, m]));
  $("r1-market").innerHTML = r.rfqs.length ? table(["ผู้ซื้อ", "ต้องการ", "ซื้อได้", "ข้อเสนอ", "ราคาต่ำสุด", "ราคาสูงสุด"], r.rfqs.map(q => {
    const m = byId[q.rfq_id] || {};
    const cell = (k, step = "1") => `<input data-mkt="${esc(q.rfq_id)}" data-k="${k}" type="number" step="${step}" value="${esc(m[k] ?? (k === "wanted_bags" ? q.bags : ""))}">`;
    return [`${esc(q.buyer || q.rfq_id)} ${typePill(q.buyer_type)} <small>${esc(q.rfq_id)}</small>`, cell("wanted_bags"), cell("bought_bags"),
      cell("offers"), cell("price_min", "0.01"), cell("price_max", "0.01")];
  }), "entry") : "";
}

// ------------------------------------------------------------------ beliefs, metrics, history
function beliefsTable() {
  const b = last?.beliefs || [];
  $("r1-beliefs").innerHTML = b.length ? table(["ผู้ซื้อ", "k สูงสุดที่ผ่าน", "k ต่ำสุดที่ถูกตัด", "ซื้อได้/ต้องการ", "ช่วงเพดานที่เชื่อ", "ติดอะไร"], b.map(x => [
    `${esc(x.buyer)} ${typePill(x.buyer_type)}`, k3(x.passed), k3(x.cut), x.fills.map(pc).join(", ") || "—",
    `<strong>${k3(x.lo)} – ${k3(x.hi)}</strong>`, x.clear_k ? `คู่แข่ง (k ตัด ≈ ${k3(x.clear_k)})` : "เพดาน"])) : '<p class="hint">ยังไม่มีผล</p>';
  const s = last?.stock || [];
  const left = s.reduce((a, x) => a + x.left, 0), total = s.reduce((a, x) => a + x.total, 0);
  $("r1-metrics").innerHTML = `<div class="metric accent"><small>PR ถ้าปิดตลาดตอนนี้</small><strong>${last?.pr_now != null ? last.pr_now.toFixed(4) : "—"}</strong></div>
    <div class="metric"><small>รายได้จากการขาย</small><strong>${f0(last?.revenue)} ฿</strong></div>
    <div class="metric"><small>กระสอบเหลือ</small><strong>${s.length ? `${left} / ${total}` : "—"}</strong></div>
    <div class="metric"><small>รอบที่กำลังดู</small><strong>${cur().number} / ${g.rounds_total}</strong></div>`;
  $("r1-text").value = last?.text || "";
  $("r1-history").innerHTML = table(["รอบ", "RFQ", "ผล", ""], g.rounds.map((r, i) => [r.number, r.rfqs.length, r.results.length,
    i === g.view ? '<span class="pill good">กำลังดู</span>' : `<button type="button" class="link" data-view="${i}">เปิด</button>`]));
  $("r1-phase").textContent = `รอบ ${cur().number}/${g.rounds_total}`;
}
function strategyPanel() {
  for (const el of $("r1-strategy").querySelectorAll("[data-c]")) {
    if (document.activeElement === el) continue;
    const v = g.ctrl[el.dataset.c];
    if (el.type === "checkbox") el.checked = el.dataset.c === "sell_round" ? v !== false : !!v;
    else el.value = v ?? "";
  }
  const c = g.ctrl;
  const bits = [c.sell_round === false ? "ไม่ขายรอบนี้" : "ขาย", {safe: "ปลอดภัย", aggressive: "กล้าเสี่ยง"}[c.risk] || "สมดุล",
    c.target_pr ? `เป้า PR ${c.target_pr}%` : "", c.ladder ? "ขั้นบันได" : "", c.undercut ? `ตัดราคา ≤${c.undercut}` : ""].filter(Boolean);
  $("r1-strategy-pill").textContent = bits.join(" · ");
  const t = last?.target || {}, e = last?.expected || {};
  const best = Math.max(0, ...(last?.beliefs || []).map(b => b.hi));
  $("r1-target").innerHTML = t.k_req != null ? `<div class="alert ${t.k_req > best ? "" : "ok"}">เป้า PR ${c.target_pr}% ต้องขายของที่เหลือเฉลี่ย
      <strong>k ${k3(t.k_req)}</strong> (ราคา = k × TV ของล็อต) · ${t.k_req <= 0.8 ? "ถึงเป้าแล้วแม้ไม่ขายเพิ่ม" :
      t.k_req > best ? `<strong>ยากมาก</strong>: สูงกว่าเพดานสูงสุดที่เชื่อ (${k3(best)})` : "เป็นไปได้"}</div>` : "";
  if (e.pr_expected != null) {
    $("r1-target").innerHTML += `<p class="hint">ถ้ายื่นตามแผนแล้วตลาดปิดหลังรอบนี้: PR คาด <strong>${e.pr_expected.toFixed(4)}</strong>
      · ถ้าขายได้ทุกซอง ${e.pr_if_all_sell.toFixed(4)} · คาดขายได้ ~${(e.bags_expected || 0).toFixed(1)} กระสอบ</p>`;
  }
}
function intelTable() {
  const rows = last?.intel || [];
  $("r1-intel").innerHTML = rows.length ? table(["ผู้ซื้อ", "RFQ ที่เห็น", "ซองเฉลี่ย", "ซื้อได้/ต้องการ", "k ต่ำสุดที่คู่แข่งขายได้", "k ตัดล่าสุด (สูงสุดที่ขายได้)", "อ่านว่า"],
    rows.map(r => [`${esc(r.buyer)} ${typePill(r.buyer_type)}`, r.rfqs, r.offers_avg == null ? "—" : r.offers_avg.toFixed(1),
      r.fill_avg == null ? "—" : pc(r.fill_avg), k3(r.k_min), k3(r.k_max_last),
      r.fill_avg != null && r.fill_avg < 0.999 ? "คนน้อย/ติดเพดาน → ชิดเพดานได้" :
        r.offers_avg >= 8 ? `แย่งกันเยอะ → ต่ำกว่า k ตัด (${k3(r.k_max_last)})` : "ปานกลาง"])) +
    '<p class="hint">k ของคู่แข่งประมาณจากราคา ÷ TV เฉลี่ยของล็อตเราที่ผ่านสเปก (ล็อตของทีมอื่นคล้ายแต่ไม่เท่ากัน)</p>' :
    '<p class="hint">กรอก "ภาพรวมตลาด" หลังจับคู่ ระบบจะสรุปว่าผู้ซื้อไหนคนแย่งเยอะ และคู่แข่งขายได้ที่ k เท่าไร</p>';
}
function render() {
  strategyPanel();
  intelTable();
  lotsTable();
  rfqRows();
  planTable();
  resultsTable();
  beliefsTable();
  for (const [id, key] of [["r1-rounds", "rounds_total"], ["r1-festival", "festival"], ["r1-floor", "floor"], ["r1-patience", "patience"]]) {
    if (document.activeElement !== $(id)) $(id).value = g[key];
  }
}

// ------------------------------------------------------------------ server
function payload(upto) {
  const c = g.ctrl;
  const lot_rules = Object.fromEntries(g.lots.filter(l => (l.mode && l.mode !== "auto") || l.min_k).map(l =>
    [l.lot_id, {mode: l.mode || "auto", bags: l.rule_bags ?? "", min_k: l.min_k ?? ""}]));
  const rfq_rules = Object.fromEntries((g.rounds[upto]?.rfqs || []).filter(q => q.rule).map(q => [q.rfq_id, {...q.rule, skip: q.rule.skip ? "1" : "0"}]));
  return {kind: "round1", floor: g.floor, patience: g.patience, probes: "1",
    sell_round: c.sell_round === false ? "0" : "1", risk: c.risk || "balanced", bags_min: c.bags_min ?? "", bags_max: c.bags_max ?? "",
    target_pr: c.target_pr ?? "", target_enforce: c.target_enforce ? "1" : "0", ladder: c.ladder ? "1" : "0",
    undercut: c.undercut ?? "", cap_bags: c.cap_bags ?? "", margin_defect: c.margin_defect ?? "", margin_moisture: c.margin_moisture ?? "",
    seed: c.seed ?? 18, lot_rules, rfq_rules, game: {
    rounds_total: Number(g.rounds_total) || 6,
    festival: String(g.festival).split(/[\s,]+/).filter(Boolean).map(Number),
    lots: g.lots.map(l => ({...l})),
    rounds: g.rounds.slice(0, upto + 1).map(r => ({number: r.number, rfqs: r.rfqs.map(q => ({...q, spec: q.spec || SPECS[normType(q.buyer_type)]})),
      results: r.results,
      // "wanted" is pre-filled with the RFQ size on screen, so use that when it was not typed
      market: r.market.map(m => ({...m, wanted_bags: m.wanted_bags ?? r.rfqs.find(q => q.rfq_id === m.rfq_id)?.bags}))})),
  }};
}
const lotReady = l => String(l.lot_id).trim() && num(l.bags) > 0 && valuation(num(l.moisture_pct), num(l.defect_pct), num(l.screen));
async function refresh(planNow = false) {
  if (!g.lots.some(lotReady)) {
    if (planNow) notice("กรอกตารางล็อตให้ครบก่อน (กระสอบ ชื้น เสีย ขนาด)", true);
    last = null;
    return render();
  }
  try {
    const data = payload(g.view);
    if (planNow) data.game.rounds[g.view].results = [];        // plan as if the round were still open
    const r = await call(data);
    last = r;
    if (planNow) {
      cur().plan = {offers: r.offers, notes: r.notes, reserve: r.reserve, unsellable: r.unsellable};
      persist();
    }
    if (r.errors?.length) notice(r.errors.join(" · "), true);
    render();
  } catch (e) {
    notice(e.message, true);
  }
}
function notice(text, bad = false) {
  const n = $("notice");
  n.hidden = !text;
  n.classList.toggle("bad", bad);
  n.textContent = text;
}

// ------------------------------------------------------------------ events
const section = $("round1");
section.addEventListener("input", e => {
  const t = e.target;
  if (t.dataset.c) {                                   // strategy panel
    g.ctrl[t.dataset.c] = t.type === "checkbox" ? t.checked : (t.value === "" ? undefined : t.value);
    persist();
    strategyPanel();
    return;
  }
  if (t.dataset.rule) {                                // per-lot rule
    const l = g.lots[Number(t.closest("[data-lot]").dataset.lot)];
    l[t.dataset.rule] = t.value === "" ? undefined : t.value;
    persist();
    if (t.dataset.rule === "mode") lotsTable();
    return;
  }
  if (t.dataset.r) {                                   // per-RFQ rule
    const q = cur().rfqs[Number(t.closest("[data-rfq]").dataset.rfq)];
    q.rule ||= {};
    q.rule[t.dataset.r] = t.type === "checkbox" ? t.checked : (t.value === "" ? undefined : t.value);
    persist();
    return;
  }
  const lotRow = t.closest("[data-lot]");
  if (lotRow && t.dataset.f) {
    g.lots[Number(lotRow.dataset.lot)][t.dataset.f] = t.value;
    persist();
    const v = valuation(num(g.lots[Number(lotRow.dataset.lot)].moisture_pct), num(g.lots[Number(lotRow.dataset.lot)].defect_pct),
      num(g.lots[Number(lotRow.dataset.lot)].screen));
    const cells = lotRow.querySelectorAll("td.calc");
    cells[0].textContent = v ? v.grade : "";
    cells[1].innerHTML = v ? `<strong>${f2(v.tv2)}</strong>` : "";
    return;
  }
  const rfqRow = t.closest("[data-rfq]");
  if (rfqRow && (t.dataset.q || t.dataset.s)) {
    const q = cur().rfqs[Number(rfqRow.dataset.rfq)];
    if (t.dataset.q) q[t.dataset.q] = t.value;
    else q.spec = {...(q.spec || SPECS.SPECIALTY), [t.dataset.s]: t.value === "" ? null : Number(t.value)};
    if (t.dataset.q === "buyer_type") {
      q.spec = normType(t.value) === "SPECIALTY" ? {...SPECS.SPECIALTY} : null;
      rfqRows();
    }
    persist();
    return;
  }
  if (t.dataset.res !== undefined) {
    cur().results[Number(t.dataset.res)][t.dataset.k] = t.value;
    persist();
    return;
  }
  if (t.dataset.mkt) {
    const r = cur();
    let m = r.market.find(x => x.rfq_id === t.dataset.mkt);
    if (!m) r.market.push(m = {rfq_id: t.dataset.mkt});
    m[t.dataset.k] = t.value;
    persist();
    return;
  }
  if (t.dataset.typed !== undefined) {
    cur().plan.offers[Number(t.dataset.typed)].typed = t.checked;
    persist();
    return;
  }
  const map = {"r1-rounds": "rounds_total", "r1-festival": "festival", "r1-floor": "floor", "r1-patience": "patience"};
  if (map[t.id]) {
    g[map[t.id]] = t.value;
    persist();
  }
});
section.addEventListener("change", e => {
  if (e.target.dataset.q === "buyer_type") rfqRows();
});
section.addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  const d = b.dataset;
  const r = cur();
  if (d.r1 === "addlot") g.lots.push({lot_id: `LOT-${g.lots.length + 1}`, bags: "", moisture_pct: "", defect_pct: "", screen: ""});
  if (d.dellot !== undefined && confirm("ลบล็อตนี้?")) g.lots.splice(Number(d.dellot), 1);
  if (d.r1 === "import") {
    const sheet = restore("tesa-sheet", []);
    if (!sheet.length) return notice("ยังไม่มีข้อมูลในใบประเมินมูลค่า", true);
    g.lots = sheet.map(s => ({lot_id: s.lot_id, bags: s.bags, moisture_pct: s.moisture, defect_pct: s.defect, screen: s.screen}));
    notice("ดึงล็อตจากใบประเมินมูลค่าแล้ว");
  }
  if (d.r1 === "addrfq") {
    const n = r.rfqs.length + 1;
    r.rfqs.push({rfq_id: `R${r.number}-${String(n).padStart(2, "0")}`, buyer: "", buyer_type: "COMMERCIAL", bags: ""});
  }
  if (d.delrfq !== undefined) r.rfqs.splice(Number(d.delrfq), 1);
  if (d.r1 === "plan") {
    if (!r.rfqs.length) return notice("ใส่ RFQ ก่อน", true);
    persist();
    return refresh(true);
  }
  if (d.r1 === "addresult") r.results.push({rfq_id: r.rfqs[0]?.rfq_id || "", lot_id: g.lots[0]?.lot_id || "", price_per_kg: "", status: "", filled_bags: ""});
  if (d.r1 === "fillresults") {
    for (const o of r.plan?.offers || []) {
      if (!r.results.some(x => x.rfq_id === o.rfq_id && x.lot_id === o.lot_id && Number(x.price_per_kg) === o.price)) {
        r.results.push({rfq_id: o.rfq_id, lot_id: o.lot_id, price_per_kg: o.price, status: "", filled_bags: ""});
      }
    }
  }
  if (d.delres !== undefined) r.results.splice(Number(d.delres), 1);
  if (d.r1 === "next") {
    if (r.results.some(x => !x.status)) return notice("เลือกผลให้ครบทุกแถว (หรือลบแถวที่ไม่ได้ยื่น)", true);
    if (g.view === g.rounds.length - 1) g.rounds.push(newRound(r.number + 1));
    g.view += 1;
    resetClock();
    notice(`บันทึกผลรอบ ${r.number} แล้ว · ใส่ RFQ ของรอบ ${r.number + 1}`);
  }
  if (d.r1 === "back" && g.view > 0) g.view -= 1;
  if (d.view !== undefined) g.view = Number(d.view);
  if (d.r1 === "reset" && confirm("ล้างทุกรอบและเริ่มใหม่? (ล็อตยังอยู่)")) {
    const lots = g.lots;
    g = fresh();
    g.lots = lots;
    resetClock();
  }
  if (d.r1 === "timer") {
    clock.running ? stopClock() : startClock();
    return;
  }
  persist();
  if (d.r1 || d.view !== undefined || d.dellot !== undefined || d.delres !== undefined || d.delrfq !== undefined) refresh(false);
});

// ------------------------------------------------------------------ 8-minute helper timer (local only)
function paint() {
  const s = Math.max(0, Math.ceil(clock.left));
  $("r1-timer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  $("r1-timer").classList.toggle("late", s <= 90);
}
function startClock() {
  clock.running = true;
  const t0 = Date.now(), left0 = clock.left;
  clock.id = setInterval(() => {
    clock.left = left0 - (Date.now() - t0) / 1000;
    paint();
    if (clock.left <= 0) stopClock();
  }, 250);
}
function stopClock() {
  clock.running = false;
  clearInterval(clock.id);
}
function resetClock() {
  stopClock();
  clock.left = 480;
  paint();
}

render();
paint();
if (g.lots.some(l => l.bags !== "")) refresh(false);
