import {specText, statusText, typePill} from "./common.js";
const $ = (id) => document.getElementById(id);
const n2 = new Intl.NumberFormat("th-TH", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const n4 = new Intl.NumberFormat("th-TH", {maximumFractionDigits: 4});
const amount = value => value == null ? "—" : n2.format(value);
const percent = value => value == null ? "—" : `${amount(100 * value)}%`;
const fmt = value => value == null ? "—" : n4.format(value);
const lotName = id => `ล็อต ${id.split("-").at(-1)}`;
const phases = ["SETUP", "APPRAISAL", "ROUND_OPEN", "ROUND_CLEARING", "CLOSED"];
const labels = ["เตรียมตัว", "ประเมินมูลค่า", "ขาย · ยื่นซอง", "จับคู่", "ปิดตลาด"];
const MODES = {
  practice: ["ตลาดฝึก (เหมือนเช้าวันที่ 17)", "เตรียมตัว 5 วินาที · ประเมิน 10 นาที · ขาย 6 รอบ รอบละ 3 นาที · จับคู่ 10 วินาที"],
  scored: ["รอบเก็บคะแนน (บ่ายวันที่ 17)", "เหมือนตลาดฝึก แต่รอบละ 8 นาที · ราว 62 นาที"],
  fast: ["ซ้อมเร็ว", "ประเมิน 2 นาที · รอบละ 1 นาที · ใช้ทดลองกลยุทธ์หลายเกม"],
  guided: ["ฝึกทีละขั้น", "ไม่จับเวลา กดปิดรับซองเองเมื่อพร้อม"],
};
const timedMode = () => state.mode && state.mode !== "guided";
const resultLabels = {
  ACCEPTED: "ขายครบ", PARTIAL: "ขายบางส่วน", ABOVE_LIMIT: "เกินเพดาน",
  OUTBID: "แพ้คิว", SPEC_MISMATCH: "ไม่ผ่านสเปก",
};
const resultHints = {
  ACCEPTED: "ใช้ราคานี้เป็นหลักฐานว่าผ่านเพดานในรอบนี้",
  PARTIAL: "ลองลดราคาเทียบ TV เพื่อขยับคิว หรือแบ่งไป RFQ อื่น",
  ABOVE_LIMIT: "ลดอัตราราคา ÷ TV แล้วติดตามผลรอบถัดไป",
  OUTBID: "ผ่านเพดานแล้ว ลองลดอัตราราคา ÷ TV หรือเลือก RFQ อื่น",
  SPEC_MISMATCH: "เลือกล็อตที่ผ่านทุกข้อในสเปกของ RFQ",
};
let state = {phase: "READY"};
let buyerId = "";
let lotId = "";
let editing = "";
let draft = null;
let candidates = [];
let trial = "";
let selectedRound = "";
let busy = false;
let polling = false;
let loaded = false;
let revision = 0;

function notice(message = "", error = false) {
  const node = $("notice");
  node.hidden = !message;
  node.classList.toggle("bad", error);
  node.textContent = message;
}
async function read(url, data) {
  const options = data ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)} : {};
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "เปิดข้อมูลไม่สำเร็จ");
  return body;
}
function marketStamp(value) {
  return JSON.stringify([value.run_id, value.phase, value.round?.round_id, value.appraisal_submitted, value.my_open_offers,
    value.paused, value.revealed]);
}
function accept(next) {
  if (next.run_id !== state.run_id || next.phase !== state.phase || next.round?.round_id !== state.round?.round_id) {
    buyerId = "";
    lotId = "";
    editing = "";
    draft = null;
    candidates = [];
    selectedRound = "";
    if (next.run_id !== state.run_id) trial = "";
  }
  state = next;
  loaded = true;
  render();
}
async function act(action, data = {}, message = "") {
  if (busy) return;
  busy = true;
  revision++;
  try {
    const next = await read("/api/action", {action, run_id: state.run_id, phase: state.phase,
      round_id: state.round?.round_id ?? null, ...data});
    if (action === "compare") {
      candidates = next.candidates;
      $("comparison").innerHTML = comparison();
      if (!candidates.length) message = "ยังไม่มีตัวเลือกจากข้อมูลที่กรอกหรือผลย้อนหลัง";
    } else {
      editing = "";
      draft = null;
      candidates = [];
      const finishedRound = action === "next" && state.phase === "ROUND_OPEN";
      accept(next);
      if (finishedRound && state.phase !== "CLOSED") $("round-results")?.scrollIntoView({block: "start"});
      if (state.phase === "CLOSED" || action === "replay" || action === "start") window.scrollTo(0, 0);
    }
    notice(message);
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
  }
}
function metric(label, value, accent = false) {
  return `<div class="metric${accent ? " accent" : ""}"><small>${label}</small><strong>${value}</strong></div>`;
}
function table(headers, rows) {
  return `<div class="table-scroll"><table><thead><tr>${headers.map(text => `<th scope="col">${text}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function line(cells) {
  return `<tr>${cells.map(text => `<td>${text}</td>`).join("")}</tr>`;
}
function dl(items) {
  return `<dl class="results">${items.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("")}</dl>`;
}
function status(text, tone = "") {
  return `<span class="pill ${tone}">${text}</span>`;
}
function intro() {
  const round = state.round;
  const at = labels[phases.indexOf(state.phase)];
  const clock = timedMode() ? (state.phase === "CLOSED" ? "จบเกม" : timer(state.seconds_left)) :
    state.phase === "CLOSED" ? "จบเกม" : "ฝึกทีละขั้น";
  return `<div class="intro"><div><p class="eyebrow">DAY 1 / SANDBOX · ${MODES[state.mode]?.[0] || ""}</p><h1>ตลาดฝึกขายกาแฟ</h1>
    <p>โรบัสต้า · ขายแบบยื่นซอง · ผู้ขายอีก 19 รายเป็นบอท · ตลาดของทีมเอง</p></div>
    ${at ? `<div class="phase-box"><span class="phase-label">${at}${round ? ` · รอบ ${round.number}/${round.of}` : ""}</span>
      <span class="timer ${state.paused ? "paused" : ""}" id="timer">${state.paused ? "⏸ " : ""}${clock}</span>
      <small class="time-note">${state.paused ? "หยุดชั่วคราว · ยื่นซองไม่ได้" : "เวลาที่เหลือของช่วงนี้"}</small></div>` : ""}</div>`;
}
function sandboxCard() {
  // slide 14: "การ์ด Sandbox ขวาล่าง" with the start-over button
  if (state.phase === "READY") return "";
  const timed = timedMode();
  return `<aside class="sandbox" aria-label="Sandbox">
    <div class="sandbox-head"><strong>Sandbox</strong><small>${MODES[state.mode]?.[0] || ""}</small></div>
    <div class="sandbox-actions">
      ${timed && state.phase !== "CLOSED" ? (state.paused ?
        '<button type="button" data-action="resume">▶ เล่นต่อ</button>' :
        '<button type="button" class="secondary" data-action="pause">⏸ หยุด</button>') : ""}
      ${state.phase !== "CLOSED" ? `<button type="button" class="secondary" data-action="${timed ? "skip" : "next"}" title="ข้ามไปช่วงถัดไป (ใช้ซ้อมเท่านั้น)">⏭ ข้ามช่วง</button>` : ""}
      <button type="button" class="secondary" data-action="restart">↻ เริ่มใหม่</button>
      <button type="button" class="secondary" data-action="reset">⟲ รีเซ็ต</button>
    </div></aside>`;
}
function timer(seconds) {
  if (seconds == null) return "จบเกม";
  const whole = Math.max(0, Math.ceil(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}
function modePicker() {
  const pick = MODES[state.mode] ? state.mode : "practice";
  return `<form id="start-form"><div class="mode-list">
    ${Object.entries(MODES).map(([key, [title, note]]) => `<label><input type="radio" name="mode" value="${key}" ${key === pick ? "checked" : ""}>
      <span>${title}<small>${note}</small></span></label>`).join("")}
    </div><div class="actions"><button name="start" value="start">เริ่มใหม่ (ล็อตชุดใหม่)</button>
    ${state.can_replay ? '<button class="secondary" name="start" value="replay">เล่นโจทย์เดิม</button>' : ""}</div></form>`;
}
function flow() {
  const t = state.timeline;
  const steps = [["เตรียมตัว", `${t?.setup_s ?? 5} วินาที`, "ล็อตกาแฟของทีมขึ้นในหน้าเว็บ"],
    ["ประเมินมูลค่า", `${Math.round((t?.appraisal_s ?? 600) / 60)} นาที`, "คิด TV แล้วกดส่ง · ส่งได้ช่วงนี้เท่านั้น"],
    [`ขาย × ${state.round?.of ?? 6} รอบ`, `รอบละ ${Math.round((t?.open_s ?? 180) / 60)} นาที`, `ยื่นข้อเสนอ แล้วรอจับคู่ ${t?.clearing_s ?? 10} วินาที ผลขึ้น`],
    ["ปิดตลาด", "ดูเฉลย", 'ของเหลือขายที่ 0.80 × TV · กด "ดูมูลค่าจริง"']];
  return `<div class="flow">${steps.map(([a, b, c], i) => `<div class="flow-step s${i}"><strong>${a}</strong><span>${b}</span><small>${c}</small></div>`).join('<span class="arrow">→</span>')}</div>`;
}
function startScreen() {
  return `<div class="start"><section class="panel"><h2>เลือกโหมด แล้วกด "เริ่มใหม่"</h2>
    <p class="hint">ล็อต ผู้ซื้อ และบอทใช้ชุดจำลองจาก TesaMarket · นาฬิกาเดินเหมือนงานจริง หยุดชั่วคราวได้</p>${modePicker()}</section>
    <section class="panel start-help"><h2>ตลาดฝึกทำงานอย่างไร</h2>${flow()}
    <p class="hint">ตลาดของทีมเอง ไม่ใช่ตลาดรวม · ล็อตสุ่มให้แต่ละทีม · ผู้ขายอีก 19 รายเป็นบอท</p></section></div>`;
}
function setupScreen() {
  return `<section class="panel"><div class="panel-head"><h2>เตรียมตัว</h2>${status("ล็อตกาแฟของทีมขึ้นแล้ว", "good")}</div>
    <p class="hint">อีกไม่กี่วินาทีจะเข้าสู่ช่วงประเมินมูลค่า ${Math.round((state.timeline?.appraisal_s ?? 600) / 60)} นาที · คัดลอกผลวัดลงใบประเมินมูลค่าได้เลย</p>
    ${stock()}</section><section class="panel">${flow()}</section>`;
}
function stock() {
  // slide 15: card "ล็อตของฉัน"
  const rows = state.lots.map(lot => {
    const s = lot.sample, calc = lot.calc;
    return line([`<span class="lot-label">${lotName(lot.lot_id)}<small>${lot.origin} · ${lot.process}</small></span>`,
      `${amount(s.moisture_pct)}%`, `${amount(s.defect_pct)}%`, s.screen, calc.grade, `<strong>${amount(calc.tv)}</strong>`,
      `<strong class="big">${lot.kg_available / state.unit}</strong><small> จาก ${lot.kg_total / state.unit}</small>`]);
  });
  return table(["ล็อต", "ความชื้น", "เมล็ดเสีย", "ขนาด", "เกรด", "TV (บาท/กก.)", "พร้อมขาย (กระสอบ)"], rows) +
    '<p class="hint">พร้อมขาย = กระสอบที่ยังไม่ได้ขาย และไม่ได้ติดอยู่ในข้อเสนอที่เปิดไว้ · ยกเลิกข้อเสนอแล้วกระสอบจะกลับมาพร้อมขาย</p>';
}
function factors() {
  return `<details><summary>ดูสูตรและตัวคูณ G, M, S</summary>${table(["ล็อต", "G", "M", "S", "TV ดิบ", "80% ของ TV", "มูลค่าล็อต (บาท)"], state.lots.map(lot => {
    const v = lot.calc;
    return line([lotName(lot.lot_id), fmt(v.g), fmt(v.m), fmt(v.s), Number(v.tv_raw).toFixed(6),
      amount(v.reserve), amount(v.lot_value)]);
  }))}<p class="hint">TV = 120 × G × M × S · ใช้ TV ดิบเทียบราคาซองและคิด PR</p></details>`;
}
function appraisal() {
  // slide 16: card "ส่งผลประเมินมูลค่า"
  const rows = state.lots.map(lot => line([`<strong>${lotName(lot.lot_id)}</strong>`,
    `ความชื้น ${amount(lot.sample.moisture_pct)}% · เมล็ดเสีย ${amount(lot.sample.defect_pct)}% · ขนาด ${lot.sample.screen} · ${lot.kg_total / state.unit} กระสอบ`,
    state.appraisal_submitted ? `<strong>${amount(lot.estimate)}</strong>` :
      `<input name="${lot.lot_id}" aria-label="มูลค่าที่ประเมิน ${lotName(lot.lot_id)}" type="number" min="0.01" step="0.01" required>`]));
  return `<section class="panel"><div class="panel-head"><h2>ส่งผลประเมินมูลค่า</h2>
    <span class="hint-right">${state.appraisal_submitted ? status("ส่งประเมินแล้ว", "good") : "ส่งได้ครั้งเดียว ก่อนตลาดเปิด"}</span></div>
    <p class="hint">ใส่มูลค่าที่ประเมินของแต่ละล็อต (บาท/กก.) จากผลวัดตัวอย่างและสูตรมูลค่า · ใบประเมินมูลค่าคิดให้ทุกล็อตพร้อมกัน</p>
    <form id="appraise">${table(["ล็อต", "ผลวัดตัวอย่าง", "มูลค่าที่ประเมิน (บาท/กก.)"], rows, "appraise")}
    ${state.appraisal_submitted ? "" : `<div class="actions"><button type="submit">ส่งผลประเมิน</button>
      <button type="button" class="secondary" data-action="copy">ใช้ TV ที่คำนวณ</button></div>`}
    </form><div class="actions">${factors()}</div>
    ${state.mode === "guided" ? `<div class="actions"><button type="button" class="secondary" data-action="next">${state.appraisal_submitted ? "เข้าสู่รอบขาย 1" : "ข้ามประเมิน (0 คะแนน)"}</button></div>` : ""}</section>
    <section class="panel"><h2>ล็อตของฉัน</h2>${stock()}</section>`;
}
function stats() {
  const s = state.standings;
  return `<div class="metrics">
    ${metric("PR หากปิดตลาดตอนนี้", percent(s.projected_pr), true)}
    ${metric("ส่วนเพิ่มเหนือ 0.80 × TV", `${amount(s.uplift)} ฿`)}
    ${metric("รายได้จากการขาย", `${amount(s.revenue_thb)} ฿`)}
    ${metric("กระสอบเหลือ", `${(s.kg_total - s.kg_sold) / state.unit} / ${s.kg_total / state.unit}`)}
  </div>`;
}
function getBuyer() {
  return state.rfqs.find(item => item.rfq_id === buyerId);
}
function eligibleOptions(buyer) {
  return buyer?.options.filter(option => option.eligible && option.max_bags > 0) ?? [];
}
function chooseDefault() {
  if (!getBuyer()) buyerId = state.rfqs[0]?.rfq_id ?? "";
  if (!editing && !eligibleOptions(getBuyer()).some(option => option.lot_id === lotId)) {
    lotId = eligibleOptions(getBuyer())[0]?.lot_id ?? "";
  }
}
function buyerTile(shownBuyer) {
  const passes = eligibleOptions(shownBuyer).length;
  const spec = shownBuyer.spec;
  return `<button type="button" class="buyer-tile" data-buyer="${shownBuyer.rfq_id}" aria-pressed="${shownBuyer.rfq_id === buyerId}">
    <span class="buyer-type">${shownBuyer.buyer_type} · ${shownBuyer.rfq_id}</span>
    <span class="buyer-name">${shownBuyer.buyer}</span>
    <small>ขอ ${shownBuyer.bags} กระสอบ · ยื่นได้ ${passes} ล็อต</small>
    <small>เสีย ≤ ${spec.max_defect_pct}% · ชื้น ≤ ${spec.max_moisture_pct}%${spec.min_screen == null ? "" : ` · ตะแกรง ≥ ${spec.min_screen}`}</small>
  </button>`;
}
function bounds(buyer, option) {
  const ev = buyer.evidence;
  if (ev.conflict) return "ขอบราคาจากรอบก่อนขัดกัน ให้ประเมินราคาใหม่ตามรอบนี้";
  if (ev.passed == null && ev.rejected == null) return "ยังไม่มีหลักฐานเพดานของผู้ซื้อรายนี้";
  return [
    ev.passed == null ? "" : `เคยผ่านที่ ${percent(ev.passed)}`,
    ev.rejected == null ? "" : `เคยถูกตัดที่ ${percent(ev.rejected)}`,
    option?.cut_price == null ? "" : `เท่ากับ ${fmt(option.cut_price)} บาท/กก. สำหรับล็อตนี้`,
  ].filter(Boolean).join(" · ");
}
function editor() {
  const buyer = getBuyer();
  if (!buyer) return "<p>กำลังรอคำขอซื้อ</p>";
  const offer = state.my_open_offers.find(item => item.offer_id === editing);
  const option = buyer.options.find(item => item.lot_id === lotId);
  const lot = state.lots.find(item => item.lot_id === lotId);
  const max = option ? Math.min(buyer.bags, option.max_bags + (offer ? offer.kg / state.unit : 0)) : 0;
  return `<div class="panel-head"><h2>${offer ? "แก้ซอง" : "ยื่นข้อเสนอขาย"}</h2>${status(buyer.rfq_id)}</div>
    <p class="hint">${buyer.buyer} · ${buyer.buyer_type}</p>
    <form id="offer-form"><div class="form-grid">
      <label class="wide">ล็อตที่จะขาย<select id="lot-select" required ${offer ? "disabled" : ""}>
        <option value="">เลือกล็อต</option>${buyer.options.map(item => `<option value="${item.lot_id}"
          ${item.lot_id === lotId ? "selected" : ""} ${!item.eligible || (!item.max_bags && item.lot_id !== offer?.lot_id) ? "disabled" : ""}>${lotName(item.lot_id)} · ${
          !item.eligible ? "ไม่ผ่านสเปก" : `${Math.min(buyer.bags, item.max_bags + (item.lot_id === offer?.lot_id ? offer.kg / state.unit : 0))} กระสอบ`
        }</option>`).join("")}</select></label>
      <label>จำนวน (กระสอบ)<input id="bags" type="number" min="1" max="${max}" step="1" required value="${offer ? offer.kg / state.unit : draft?.bags ?? ""}" ${max ? "" : "disabled"}></label>
      <label>ราคา (บาท/กก.)<input id="ask" type="number" min="0.01" max="1000" step="0.01" required value="${offer?.price_per_kg ?? draft?.price ?? ""}" ${max ? "" : "disabled"}></label>
    </div>
    ${lot ? `<div class="detail-box">TV ${amount(lot.calc.tv)} · เริ่มเป็นบวกที่ <strong>${amount(lot.calc.min_positive)} บาท/กก.</strong><br>${bounds(buyer, option)}</div>` : ""}
    <div class="actions">${option?.past_price != null && max ? `<button type="button" class="secondary" data-use-price="${option.past_price}">ใช้ราคาอิงผลเดิม ${amount(option.past_price)}</button>` : ""}
    <button type="submit" ${max ? "" : "disabled"}>${offer ? "บันทึกการแก้ไข" : "ยื่นซอง"}</button>
    ${offer ? '<button type="button" class="secondary" data-action="stop-edit">กลับไปยื่นซองใหม่</button>' : ""}</div>
    <output id="quote" aria-live="polite"></output></form>`;
}
function refreshQuote() {
  const lot = state.lots.find(item => item.lot_id === lotId);
  const option = getBuyer()?.options.find(item => item.lot_id === lotId);
  const target = $("quote");
  if (!lot || !target) return;
  const price = $("ask").valueAsNumber;
  const bags = $("bags").valueAsNumber;
  if (!(price > 0 && bags > 0)) {
    target.textContent = "";
    return;
  }
  const ratio = price / Number(lot.calc.tv_raw);
  const gain = (price - lot.calc.reserve) * bags * state.unit;
  const warn = option?.cut_price != null && price >= option.cut_price - 1e-10 ? "แตะหรือเกินขอบที่เคยถูกตัด" :
    gain <= 0 ? "ราคาอยู่ที่หรือต่ำกว่าระดับระบายของเหลือ" : null;
  target.classList.toggle("error", !!warn);
  target.innerHTML = dl([["ราคา ÷ TV", percent(ratio)], ["ส่วนเพิ่มถ้าขายครบ", `${amount(gain)} บาท`]]) +
    (warn ? `<small class="negative">${warn}</small>` : "");
}
function liveOffers() {
  if (!state.my_open_offers.length) return `<p class="hint">ยังไม่ได้ยื่นซองในรอบนี้</p>`;
  return table(["คำขอซื้อ", "ล็อต", "กระสอบ", "บาท/กก.", "ราคา ÷ TV", "จัดการ"], state.my_open_offers.map(offer => line([
    state.rfqs.find(q => q.rfq_id === offer.rfq_id)?.buyer || offer.rfq_id, lotName(offer.lot_id),
    offer.kg / state.unit, amount(offer.price_per_kg), percent(offer.ratio),
    state.phase === "ROUND_OPEN" ? `<button class="secondary" data-edit="${offer.offer_id}">แก้ไข</button>
      <button class="secondary" data-cancel="${offer.offer_id}">ยกเลิก</button>` : status("รอผล"),
  ])));
}
function comparison() {
  if (!candidates.length) return "";
  return table(["ผู้ซื้อ / ล็อต", "กระสอบ", "บาท/กก.", "ราคา ÷ TV", "ส่วนเพิ่มถ้าขายครบ", "Δ PR (จุด %)", "หลักฐาน"], candidates.map((row, index) => line([
    `<button type="button" class="secondary" data-choose="${index}">${row.buyer} · ${lotName(row.lot_id)}</button>`,
    row.bags, amount(row.price), percent(row.ratio), `${amount(row.uplift)} ฿`, `${amount(row.delta_pr * 100)}`,
    row.above_past_cut ? status("เกินขอบเดิม", "warn") : row.evidence_conflict ? status("ขอบเดิมขัดกัน", "warn") :
    row.uplift <= 0 ? status("ส่วนเพิ่มไม่เป็นบวก", "warn") : status("ขึ้นกับผลจับคู่"),
  ])));
}
function roundResults() {
  // slide 20: "ผลรอบล่าสุด" + "ภาพรวมตลาดรอบล่าสุด"
  if (!state.history.length) return "";
  const result = state.history.find(row => row.round_id === selectedRound) || state.history.at(-1);
  const offers = result.my_offers.filter(row => row.status !== "CANCELLED");
  const range = row => row.price_min == null ? "–" : `${amount(row.price_min)}–${amount(row.price_max)} (เฉลี่ย ${amount(row.price_avg)})`;
  return `<section class="panel" id="round-results"><div class="panel-head"><h2>ผลรอบล่าสุด</h2>
    <label class="inline-label">รอบ<select id="history-round">
      ${state.history.map(row => `<option value="${row.round_id}" ${row.round_id === result.round_id ? "selected" : ""}>${row.round_id}</option>`).join("")}
    </select></label></div>
    ${offers.length ? table(["คำขอซื้อ (RFQ)", "ล็อต", "เสนอ (กระสอบ)", "ขายได้", "บาท/กก.", "k = ราคา ÷ TV", "ผล", "ทำอะไรต่อ"], offers.map(row => line([
      row.rfq_id, lotName(row.lot_id), row.kg / state.unit, row.filled_kg / state.unit, amount(row.price_per_kg),
      fmt(row.ratio), statusText(row.status), `<small>${resultHints[row.status] || ""}</small>`,
    ]))) : '<p class="hint">รอบนี้ไม่มีซองที่ส่งเข้าจับคู่</p>'}
    <div class="panel-head sub-head"><h3>ภาพรวมตลาดรอบล่าสุด</h3><small>ไม่ระบุทีม</small></div>
    ${table(["ผู้ซื้อ", "ต้องการ", "ซื้อได้", "ข้อเสนอ", "ราคาที่ขายได้ (บาท/กก.)"], result.market_summary.map(row => line([
      `${row.buyer} ${typePill(row.buyer_type)}`, row.wanted_kg / state.unit, row.bought_kg / state.unit, row.offers ?? "–", range(row),
    ])))}</section>`;
}
function rfqRow(q) {
  const mine = state.my_open_offers.filter(o => o.rfq_id === q.rfq_id);
  const rec = (recommendation?.round_id === state.round?.round_id ? recommendation.offers : []).filter(o => o.rfq_id === q.rfq_id);
  const options = q.options.map(o => {
    const lot = state.lots.find(l => l.lot_id === o.lot_id);
    return `<option value="${o.lot_id}" ${!o.eligible || !o.max_bags ? "disabled" : ""}>${lotName(o.lot_id)} · ${
      !o.eligible ? "ไม่ผ่านสเปก" : `${lot.kg_available / state.unit} กระสอบ`}</option>`;
  });
  const first = q.options.find(o => o.eligible && o.max_bags);
  return `<form class="rfq-row ${mine.length ? "offered" : ""}" data-rfq="${q.rfq_id}">
    <div class="rfq-main">
      <div class="rfq-line"><strong>${Number(q.rfq_id.split("-").at(-1))} ${q.buyer}</strong> ${typePill(q.buyer_type)} ต้องการ <strong>${q.bags}</strong> กระสอบ</div>
      <div class="rfq-spec">${specText(q.spec)} · ${q.rfq_id}</div>
      ${mine.map(o => `<div class="rfq-done">✓ เสนอแล้ว ${o.kg / state.unit} กระสอบ @ ${amount(o.price_per_kg)}</div>`).join("")}
      ${rec.map(o => `<button type="button" class="chip" data-fill='${JSON.stringify({lot: o.lot_id, bags: o.bags, price: o.price})}'>แนะนำ ${
        o.lot_id.split("-").at(-1)} · ${o.bags} กระสอบ @ ${amount(o.price)} (k ${fmt(o.k)})</button>`).join("")}
    </div>
    <div class="rfq-form">
      <select name="lot" aria-label="ล็อตสำหรับ ${q.rfq_id}" ${first ? "" : "disabled"}>${options.join("")}</select>
      <label class="inline-label">กระสอบ<input name="bags" type="number" min="1" max="${q.bags}" step="1" value="1" required></label>
      <label class="inline-label">ราคา<input name="price" type="number" min="0.01" max="1000" step="0.01" placeholder="บาท/กก." required></label>
      <button type="submit" ${first ? "" : "disabled"}>ส่งข้อเสนอ</button>
      <small class="rfq-k" aria-live="polite"></small>
    </div></form>`;
}
function rfqKHint(form) {
  const lot = state.lots.find(l => l.lot_id === form.elements.lot.value);
  const price = form.elements.price.valueAsNumber;
  const out = form.querySelector(".rfq-k");
  if (!lot || !(price > 0)) return (out.textContent = "");
  const k = price / Number(lot.calc.tv_raw);
  out.textContent = `k = ${k.toFixed(3)}${k <= 0.8 ? " · ต่ำกว่า 0.80 แย่กว่าไม่ขาย" : ""}`;
  out.classList.toggle("negative", k <= 0.8);
}
function trading() {
  chooseDefault();
  return `${stats()}
    ${state.phase === "ROUND_OPEN" ? `<section class="panel"><div class="panel-head"><h2>คำขอซื้อ (RFQ) รอบนี้</h2>${status(state.round.calendar)}</div>
      <p class="hint">RFQ = Request for Quotation ผู้ซื้อประกาศว่าต้องการกี่กระสอบ และคุณภาพแบบไหน</p>
      <div class="rfq-list">${state.rfqs.map(rfqRow).join("")}</div></section>
      ${editing ? `<section class="panel composer" id="editor">${editor()}</section>` : '<div id="editor" hidden></div>'}` :
      `<section class="panel"><h2>กำลังจับคู่ซอง</h2><p class="hint">ตลาดเทียบราคา ÷ TV และตัดซองที่เกินเพดานก่อนจัดสรร</p></section>`}
    <section class="panel"><div class="panel-head"><h2>ซองของฉัน</h2>${state.mode === "guided" ?
      '<button data-action="next">ปิดรับซองและจับคู่</button>' : ""}</div>${liveOffers()}</section>
    ${state.phase === "ROUND_OPEN" ? `<section class="panel"><h2>เปรียบเทียบผู้ซื้อและล็อต</h2>
      <p class="hint">เรียงตามส่วนเพิ่มถ้าขายครบทีละซอง เว้นอัตราไว้เพื่อใช้เฉพาะราคาที่อ้างอิงผลเดิมได้</p>
      <form id="compare" class="inline-form"><label>ราคา ÷ TV ที่จะลอง (%)<input name="markup" type="number" min="0.01" step="any" value="${trial}"></label>
      <button type="submit" class="secondary">เปรียบเทียบ</button></form><div id="comparison">${comparison()}</div></section>` : ""}
    ${state.phase === "ROUND_OPEN" ? `<section class="panel advice"><div class="panel-head"><h2>คำแนะนำจาก planner (k*)</h2>
      <span class="pill">ตลาดฝึกเท่านั้น</span></div>
      <p class="hint">เรียนรู้เพดานจากผลทุกรอบที่ผ่านมา แล้วเลือก k ที่ให้ (k − V) × โอกาสขาย สูงสุดต่อคู่ล็อต × RFQ</p>
      <div class="actions"><button type="button" class="secondary" data-action="recommend">คำนวณซองที่แนะนำ</button></div>
      <div id="recommend">${advice()}</div></section>` : ""}
    <section class="panel"><h2>ล็อตของฉัน</h2>${stock()}<div class="actions">${factors()}</div></section>
    ${roundResults()}`;
}
let recommendation = null;
function advice() {
  const r = recommendation;
  if (!r || r.round_id !== state.round?.round_id) return "";
  const rows = r.offers.map(o => line([`${o.buyer}<small> ${o.buyer_type} · ${o.rfq_id}</small>`, lotName(o.lot_id), o.bags,
    `<strong>${amount(o.price)}</strong>`, fmt(o.k), percent(o.p), `${amount(o.ev)} ฿`,
    status(o.kind === "probe" ? "ทดลอง" : "หลัก", o.kind === "probe" ? "warn" : "good")]));
  const beliefs = r.beliefs.map(b => line([`${b.buyer}<small> ${b.buyer_type}</small>`, `${fmt(b.lo)} – ${fmt(b.hi)}`,
    b.clear_k ? fmt(b.clear_k) : "ติดเพดาน", b.source]));
  return (r.offers.length ? table(["ผู้ซื้อ", "ล็อต", "กระสอบ", "บาท/กก.", "k", "โอกาสขาย", "EV เหนือ V", ""], rows) :
      '<p class="hint">ไม่มีซองที่คุ้มกว่าถือไว้ในรอบนี้</p>') +
    `<p class="hint">${r.notes.join(" · ")} · V: ${Object.entries(r.reserve).map(([k, v]) => `${lotName(k)} ${fmt(v)}`).join(", ")}${
      r.unsellable.length ? ` · ไม่ผ่านสเปกใคร: ${r.unsellable.map(lotName).join(", ")}` : ""}</p>
    <details><summary>เพดานที่เชื่อต่อ RFQ</summary>${table(["ผู้ซื้อ", "ช่วงเพดาน k", "k ตัดคู่แข่ง", "ที่มา"], beliefs)}</details>
    ${r.offers.length ? `<div class="actions"><button type="button" data-action="apply-plan">ยื่นตามคำแนะนำ (แทนซองเดิมทั้งหมด)</button></div>` : ""}`;
}
async function recommend() {
  try {
    const r = await read("/api/action", {action: "recommend", run_id: state.run_id, phase: state.phase, round_id: state.round?.round_id ?? null});
    recommendation = {...r, round_id: state.round?.round_id};
    render();                                  // RFQ rows also show the recommended chips
    notice();
  } catch (error) {
    notice(error.message, true);
  }
}
function closed() {
  if (!state.revealed) {
    return `<section class="panel reveal"><h2>ปิดตลาดแล้ว</h2>
      <p class="hint">ของที่เหลือขายให้อัตโนมัติที่ 0.80 × TV · กดเพื่อดูมูลค่าจริง คะแนน และอันดับ</p>
      <div class="actions"><button type="button" data-action="reveal">ดูมูลค่าจริง</button></div></section>${roundResults()}`;
  }
  const score = state.score;
  return `<div class="metrics">${metric("คะแนนรวม / 100", amount(score.total), true)}
    ${metric("PR", percent(score.pr))}${metric("คะแนนขาย / 80", amount(score.pr_score))}
    ${metric("คะแนนประเมิน / 20", amount(score.appraisal_score))}</div>
    <section class="panel"><div class="panel-head"><h2>มูลค่าจริงหลังปิดตลาด</h2>${status(`อันดับ ${state.rank} จาก 20`, "good")}</div>
    <p class="hint">PR ผู้นำในตลาดฝึก ${percent(state.best_pr)} · MAPE ${percent(score.mape)} · ระบายของเหลือ ${amount(score.salvage_thb)} บาท · ส่วนเพิ่ม ${amount(state.standings.uplift)} บาท</p>
    ${table(["ล็อต", "TV จริง", "ที่ประเมิน", "กระสอบที่ขายได้", "กระสอบที่เหลือ"], state.lots.map(lot => line([
      lotName(lot.lot_id), amount(lot.calc.tv), amount(lot.estimate), (lot.kg_total - lot.kg_remaining) / state.unit,
      lot.kg_remaining / state.unit,
    ])))}</section>${roundResults()}
    <section class="panel"><h2>ลองกลยุทธ์รอบใหม่</h2>${modePicker()}</section>`;
}
function render() {
  const index = phases.indexOf(state.phase);
  const content = index === -1 ? startScreen() : state.phase === "SETUP" ? setupScreen() : state.phase === "APPRAISAL" ? appraisal() :
    state.phase === "CLOSED" ? closed() : trading();
  $("game").innerHTML = `${intro()}${index === -1 ? "" : `<ol class="stages" aria-label="ขั้นตอนตลาด">${labels.map((name, i) =>
    `<li ${i === index ? 'class="current" aria-current="step"' : ""}>${name}</li>`).join("")}</ol>`}${content}${sandboxCard()}`;
  refreshQuote();
}
function updateEditor() {
  $("editor").innerHTML = editor();
  $("editor").scrollIntoView({block: "nearest"});
  refreshQuote();
}
$("market").addEventListener("click", event => {
  const {action, buyer, edit, cancel, usePrice, choose} = event.target.closest("button")?.dataset || {};
  if (action === "next") {
    if (state.phase === "APPRAISAL" && !state.appraisal_submitted && !confirm("ข้ามประเมินและได้ 0 คะแนนส่วนนี้?")) return;
    act("next");
  }
  if (action === "copy") {
    for (const lot of state.lots) $("appraise").elements[lot.lot_id].value = lot.calc.tv.toFixed(2);
    notice("ใส่ TV ที่คำนวณให้แล้ว ตรวจค่าก่อนกดส่ง");
  }
  if (buyer) {
    editing = "";
    draft = null;
    buyerId = buyer;
    lotId = eligibleOptions(getBuyer())[0]?.lot_id ?? "";
    document.querySelectorAll("[data-buyer]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.buyer === buyer)));
    updateEditor();
  }
  if (edit) {
    const offer = state.my_open_offers.find(row => row.offer_id === edit);
    editing = edit;
    draft = null;
    buyerId = offer.rfq_id;
    lotId = offer.lot_id;
    render();
    $("editor").scrollIntoView({block: "nearest"});
  }
  if (cancel) act("cancel", {offer_id: cancel}, "ยกเลิกซองและคืนกระสอบแล้ว");
  if (action === "recommend") recommend();
  if (["pause", "resume", "skip", "reveal"].includes(action)) act(action, {}, {pause: "หยุดนาฬิกาแล้ว", resume: "เล่นต่อ", skip: "ข้ามไปช่วงถัดไป", reveal: ""}[action]);
  if (action === "restart" && confirm("เริ่มใหม่ด้วยล็อตชุดใหม่? เกมนี้จะหายไป")) act("start", {mode: state.mode}, "เริ่มเกมใหม่แล้ว");
  if (action === "reset" && confirm("รีเซ็ตกลับหน้าเลือกโหมด?")) act("reset", {}, "");
  const fill = event.target.closest("[data-fill]")?.dataset.fill;
  if (fill) {
    const f = event.target.closest("form.rfq-row").elements, o = JSON.parse(fill);
    f.lot.value = o.lot;
    f.bags.value = o.bags;
    f.price.value = o.price;
    rfqKHint(event.target.closest("form.rfq-row"));
  }
  if (action === "apply-plan" && confirm("ยื่นซองตามคำแนะนำ? ซองที่เปิดอยู่จะถูกแทนที่ (ตลาดฝึกบนเครื่องเท่านั้น)")) {
    act("apply_plan", {}, "ยื่นซองตามคำแนะนำแล้ว");
  }
  if (action === "stop-edit") {
    editing = "";
    lotId = eligibleOptions(getBuyer())[0]?.lot_id ?? "";
    updateEditor();
  }
  if (usePrice) {
    $("ask").value = usePrice;
    refreshQuote();
  }
  if (choose !== undefined) {
    const row = candidates[Number(choose)];
    if (!row) return;
    buyerId = row.rfq_id;
    lotId = row.lot_id;
    draft = {price: row.price, bags: row.bags};
    editing = "";
    render();
    $("editor").scrollIntoView({block: "nearest"});
  }
});
$("market").addEventListener("change", event => {
  if (event.target.id === "lot-select") {
    lotId = event.target.value;
    draft = null;
    updateEditor();
    $("lot-select").focus();
  } else if (event.target.id === "history-round") {
    selectedRound = event.target.value;
    const select = event.target;
    select.closest(".panel").outerHTML = roundResults();
    $("history-round").focus();
  }
});
$("market").addEventListener("input", event => {
  const row = event.target.closest("form.rfq-row");
  if (row) rfqKHint(row);
  if (["ask", "bags"].includes(event.target.id)) refreshQuote();
});
$("market").addEventListener("submit", event => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  if (form.id === "start-form") act(event.submitter?.value || "start", {mode: data.mode});
  if (form.id === "appraise") {
    if (!confirm("ส่งผลประเมินทั้ง 4 ล็อต? ส่งได้ครั้งเดียว")) return;
    const items = state.lots.map(lot => ({lot_id: lot.lot_id, est_value_per_kg: Number(data[lot.lot_id])}));
    act("appraise", {items}, "ส่งผลประเมินแล้ว");
  }
  if (form.classList.contains("rfq-row")) {
    const f = form.elements;
    act("offer", {rfq_id: form.dataset.rfq, lot_id: f.lot.value, bags: f.bags.valueAsNumber, price_per_kg: f.price.valueAsNumber},
      `ยื่นซอง ${form.dataset.rfq} แล้ว`);
  }
  if (form.id === "offer-form") {
    const order = {bags: $("bags").valueAsNumber, price_per_kg: $("ask").valueAsNumber};
    if (editing) act("edit", {...order, offer_id: editing}, "แก้ไขซองแล้ว");
    else act("offer", {...order, rfq_id: buyerId, lot_id: lotId}, "ยื่นซองแล้ว");
  }
  if (form.id === "compare") {
    trial = data.markup;
    act("compare", {markup: trial});
  }
});
document.querySelector("nav").addEventListener("click", event => {
  const active = event.target.closest("button")?.dataset.view;
  if (!active) return;
  showView(active);
  notice();
});
function showView(active) {
  if (!$(active)) active = "market";
  document.querySelectorAll("nav button").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === active)));
  for (const id of ["market", "tools", "round1", "round2", "strategy"]) $(id).hidden = id !== active;
  try { localStorage.setItem("tesa-view", active); } catch { /* ignore */ }
}
try { showView(localStorage.getItem("tesa-view") || "market"); } catch { showView("market"); }
$("score-lots").innerHTML = table(["ล็อต", "TV ดิบ", "TV ที่ส่ง", "กระสอบ"], Array.from({length: 4}, (_, i) => line([
  `ล็อต ${i + 1}`, ...["tv", "estimate", "bags"].map(name =>
    `<input name="${name}${i}" aria-label="${{tv: "TV ดิบ", estimate: "TV ที่ส่ง", bags: "กระสอบ"}[name]} ล็อต ${i + 1}"
      type="number" min="0" step="${name === "bags" ? "1" : "any"}">`),
])));
let unlockedTv = null;
$("tools").addEventListener("submit", async event => {
  event.preventDefault();
  const kind = event.target.id;
  const data = Object.fromEntries(new FormData(event.target));
  const out = $(`${kind}-result`);
  if (kind === "score" && $("estimate-inputs").open) {
    data.appraisal = Array.from({length: 4}, (_, i) => ({tv: data[`tv${i}`], estimate: data[`estimate${i}`], bags: data[`bags${i}`]}));
  }
  try {
    const result = await read("/api/calculate", {kind, ...data});
    out.classList.remove("error");
    if (kind === "valuation") {
      unlockedTv = result.tv_raw;
      out.innerHTML = dl([["เกรด", result.grade], ["G · M · S", `${fmt(result.g)} · ${fmt(result.m)} · ${fmt(result.s)}`],
        ["TV สำหรับส่ง (บาท/กก.)", amount(result.tv)], ["TV ดิบ (บาท/กก.)", Number(result.tv_raw).toFixed(8)],
        ["ราคาเริ่มเป็นบวก (บาท/กก.)", amount(result.min_positive)], ["มูลค่าทั้งล็อต (บาท)", amount(result.lot_value)]]) +
        (result.tv > 0 ? '<div class="actions"><button type="button" class="secondary" id="to-price">ใช้ TV ดิบเทียบราคา</button></div>' : "");
    } else if (kind === "price") {
      out.innerHTML = dl([["ราคา ÷ TV", percent(result.ratio)], ["จุดคุ้มเท่าระบาย (บาท/กก.)", fmt(result.reserve)],
        ["ราคาเริ่มเป็นบวก (บาท/กก.)", amount(result.min_positive)], ["ราคาอิงที่เคยผ่าน (บาท/กก.)", amount(result.past_pass_price)],
        ["ขอบที่เคยถูกตัด (บาท/กก.)", fmt(result.past_cut_price)], ["รายได้ (บาท)", amount(result.revenue)],
        ["ส่วนเพิ่มจาก 0.80 × TV (บาท)", amount(result.uplift)]]) +
        (result.conflict ? '<p class="negative">ขอบราคาขัดกัน ให้ตรวจข้อมูลผลขายเดิม</p>' : "");
    } else {
      out.innerHTML = dl([["PR", percent(result.pr)], ["ระบายของเหลือ (บาท)", amount(result.salvage)],
        ["ส่วนเพิ่มจากการระบายทั้งหมด (บาท)", amount(result.uplift)], ["MAPE", percent(result.mape)],
        ["คะแนนประเมิน / 20", amount(result.appraisal_score)], ["คะแนนขาย / 80", amount(result.pr_score)], ["รวม / 100", amount(result.total)]]);
    }
  } catch (error) {
    out.classList.add("error");
    out.textContent = error.message;
  }
});
$("tools").addEventListener("click", event => {
  if (event.target.id !== "to-price" || !unlockedTv) return;
  $("price").elements.tv.value = unlockedTv;
  $("price").elements.price.focus();
});
async function poll() {
  if (busy || polling) return;
  polling = true;
  const startedAt = revision;
  try {
    const next = await read("/api/state");
    if (busy || startedAt !== revision) return;
    if (!loaded || marketStamp(next) !== marketStamp(state)) accept(next);
    else if (next.mode && next.mode !== "guided" && $("timer") && !next.paused && next.phase !== "CLOSED") $("timer").textContent = timer(next.seconds_left);
  } catch (error) {
    if (!loaded) notice("ติดต่อเว็บฝึกไม่ได้ กรุณาเปิด Python แล้วโหลดใหม่", true);
  } finally {
    polling = false;
  }
}
poll();
setInterval(poll, 1000);
