// Round 2-3 (18 & 20 Nov): the agent submits. This page only configures, tests offline, and READS the agent's logs.
import {$, call, esc, f0, f2, k3, pc, statusText, table, typePill} from "./common.js";

const env = $("r2-env");
function envText() {
  const lines = [...env.querySelectorAll("input, select")].map(x => `${x.name}=${x.value}`);
  $("r2-env-out").textContent = "# เพิ่มบรรทัดเหล่านี้ใน .env (ค่าอื่นของผู้จัดคงไว้)\n" + lines.join("\n") +
    "\nLLM_MODEL=event-llm      # ห้ามใช้ LLM อื่นตอนแข่ง";
}
env.addEventListener("input", envText);
envText();

async function preflight(button) {
  button.disabled = true;
  $("r2-preflight").innerHTML = '<p class="hint">กำลังเล่นเกมทดสอบ…</p>';
  try {
    const r = await call({kind: "preflight", preset: $("r2-preset").value, games: $("r2-games").value});
    const rows = r.rows.map((x, i) => [i + 1, x.pr.toFixed(4), x.best_bot_pr.toFixed(4), `${(100 * x.mape).toFixed(2)}%`,
      x.appraisal.toFixed(1), x.sell.toFixed(1), `<strong>${x.total.toFixed(1)}</strong>`, `${x.rank} / ${x.teams}`]);
    const mean = k => r.rows.reduce((a, x) => a + x[k], 0) / r.rows.length;
    $("r2-preflight").innerHTML = table(["เกม", "PR", "PR บอทดีสุด", "MAPE", "ประเมิน /20", "ขาย /80", "รวม", "อันดับ"], rows) +
      `<p class="hint">เฉลี่ย ${mean("total").toFixed(1)} คะแนน · ${r.seconds} วินาที · ไม่มีซองถูกปฏิเสธ (ถ้ามีจะขึ้น error)</p>`;
  } catch (e) {
    $("r2-preflight").innerHTML = `<p class="negative">${esc(e.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

function render(r) {
  const vals = r.env.values;
  $("r2-env-file").innerHTML = r.env.exists ? `<p class="hint">.env ตอนนี้: ${Object.entries(vals).filter(([k]) =>
    ["MARKET_URL", "TEAM_KEY", "USE_LLM", "LLM_MODEL", "LLM_API_KEY", "PATIENCE", "K_FLOOR", "PROBES", "SAMPLE_NOISE"].includes(k))
    .map(([k, v]) => `<code>${esc(k)}</code> ${esc(v)}`).join(" · ")}</p>` +
    (vals.LLM_MODEL && vals.LLM_MODEL !== "event-llm" ? '<p class="negative">LLM_MODEL ไม่ใช่ event-llm → ใช้แข่งไม่ได้</p>' : "") :
    '<p class="hint">ยังไม่มีไฟล์ .env (คัดลอกจาก .env.example)</p>';
  const lastEv = r.last_event;
  $("r2-metrics").innerHTML = `<div class="metric accent"><small>รายได้ที่ขายแล้ว</small><strong>${f0(r.revenue)} ฿</strong></div>
    <div class="metric"><small>รอบที่มีผล</small><strong>${r.rounds.length}</strong></div>
    <div class="metric"><small>เหตุการณ์ล่าสุด</small><strong class="small">${lastEv ? `${esc(lastEv.time?.slice(11) || "")} ${esc(lastEv.event)}` : "—"}</strong></div>
    <div class="metric"><small>ไฟล์ memory</small><strong class="small">${esc(r.memory || "—")}</strong></div>`;
  $("r2-problems").innerHTML = r.problems.length ? `<div class="alert"><strong>ต้องดู:</strong><br>${r.problems.map(p =>
    `${esc(p.time?.slice(11) || "")} ${esc(p.event)} ${esc(p.error || "")}`).join("<br>")}</div>` : "";
  $("r2-sent").innerHTML = r.sent.length ? table(["เวลา", "รอบ", "ที่มา", "ซอง", "กก.", "วินาที", "รายละเอียด"], r.sent.slice().reverse().map(s => [
    esc(s.time?.slice(11)), esc(s.round), esc(s.source), s.n, s.kg, s.seconds,
    s.offers.map(o => `${esc(o.rfq_id)} ${esc(String(o.lot_id).slice(-6))} ${o.kg / 60}b @${f2(o.price_per_kg)}`).join("<br>") +
      (s.notes.length ? `<small>${s.notes.map(esc).join(" · ")}</small>` : "")])) : '<p class="hint">ยังไม่มี log · เริ่ม agent ด้วย python agent/main.py</p>';
  const lastRound = r.rounds.at(-1);
  $("r2-results").innerHTML = lastRound ? `<p class="hint">รอบ ${esc(lastRound.round_id)}${lastRound.kind === "practice" ? " (ซ้อม ไม่นับคะแนน)" : ""}</p>` +
    table(["RFQ", "ล็อต", "เสนอ", "ขายได้", "บาท/กก.", "k", "ผล"], lastRound.my_offers.map(o => [esc(o.rfq_id), esc(String(o.lot_id).slice(-8)),
      o.kg / 60, o.filled_kg / 60, f2(o.price_per_kg), k3(o.k), statusText(o.status)])) +
    table(["ผู้ซื้อ", "ต้องการ", "ซื้อได้", "ข้อเสนอ", "ราคาที่ขายได้"], lastRound.market_summary.map(m => [
      `${esc(m.buyer)} ${typePill(m.buyer_type)}`, m.wanted_kg / 60, m.bought_kg / 60, m.offers ?? "—",
      m.price_min == null ? "—" : `${f2(m.price_min)}–${f2(m.price_max)} (เฉลี่ย ${f2(m.price_avg)})`])) : '<p class="hint">ยังไม่มีผล</p>';
  $("r2-beliefs").innerHTML = r.beliefs.length ? table(["ผู้ซื้อ", "k ผ่านสูงสุด", "k ถูกตัดต่ำสุด", "ซื้อได้/ต้องการ", "ช่วงเพดาน", "ติดอะไร"], r.beliefs.map(b => [
    `${esc(b.buyer)} ${typePill(b.buyer_type)}`, k3(b.passed), k3(b.cut), b.fills.slice(-4).map(pc).join(", "),
    `<strong>${k3(b.lo)} – ${k3(b.hi)}</strong>`, b.clear_k ? `คู่แข่ง ≈ ${k3(b.clear_k)}` : "เพดาน"])) : '<p class="hint">ยังไม่มีผล</p>';
}

async function refresh() {
  try {
    render(await call({kind: "agent"}));
  } catch (e) {
    $("r2-sent").innerHTML = `<p class="negative">${esc(e.message)}</p>`;
  }
}
$("round2").addEventListener("click", e => {
  const action = e.target.closest("[data-r2]")?.dataset.r2;
  if (action === "preflight") preflight(e.target.closest("button"));
  if (action === "refresh") refresh();
});
setInterval(() => {
  if (!$("round2").hidden && $("r2-auto").checked && document.visibilityState === "visible") refresh();
}, 5000);
new MutationObserver(() => { if (!$("round2").hidden) refresh(); }).observe($("round2"), {attributes: true, attributeFilter: ["hidden"]});
