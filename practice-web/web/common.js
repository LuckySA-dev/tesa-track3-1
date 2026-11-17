// Shared helpers for the strategy pages. Everything stays on this machine (local Python server only).
export const $ = id => document.getElementById(id);
const nf2 = new Intl.NumberFormat("th-TH", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const nf0 = new Intl.NumberFormat("th-TH", {maximumFractionDigits: 0});
export const f2 = v => v == null || Number.isNaN(v) ? "—" : nf2.format(v);
export const f0 = v => v == null || Number.isNaN(v) ? "—" : nf0.format(v);
export const f4 = v => v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(4);
export const k3 = v => v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(3);
export const pc = v => v == null || Number.isNaN(v) ? "—" : `${(100 * v).toFixed(1)}%`;
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

export async function call(data) {
  const r = await fetch("/api/strategy", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || "คำนวณไม่สำเร็จ");
  return body;
}
export function table(headers, rows, cls = "") {
  return `<div class="table-scroll"><table class="${cls}"><thead><tr>${headers.map(h => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r => Array.isArray(r) ? `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>` : r).join("")}</tbody></table></div>`;
}
export function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked: page still works */ }
}
export function restore(key, fallback) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
}

export const TYPES = {COMMERCIAL: "Commercial", INSTANT: "Instant", SPECIALTY: "Specialty"};
export const SHORT = {COM: "COMMERCIAL", INS: "INSTANT", SPEC: "SPECIALTY", SPE: "SPECIALTY"};
export const normType = t => SHORT[String(t || "").toUpperCase()] || String(t || "").toUpperCase();
export const typePill = t => `<span class="type ${normType(t).toLowerCase()}">${TYPES[normType(t)] || esc(t)}</span>`;
export const SPECS = {
  COMMERCIAL: {max_defect_pct: 7, max_moisture_pct: 12.5, min_screen: null},
  INSTANT: {max_defect_pct: 15, max_moisture_pct: 14, min_screen: null},
  SPECIALTY: {max_defect_pct: 3, max_moisture_pct: 12.2, min_screen: 16},
};
export const specText = s => `เมล็ดเสีย ≤${s.max_defect_pct}% · ความชื้น ≤${s.max_moisture_pct}%${s.min_screen == null ? "" : ` · ตะแกรง ≥${s.min_screen}`}`;
export const STATUS = {
  ACCEPTED: ["ขายได้ทั้งหมด", "good"], PARTIAL: ["ขายได้บางส่วน", "good"], ABOVE_LIMIT: ["แพงเกินเพดานผู้ซื้อ", "bad"],
  OUTBID: ["แพ้ข้อเสนอที่คุ้มกว่า", "bad"], SPEC_MISMATCH: ["คุณภาพไม่ผ่านสเปก", "bad"], CANCELLED: ["ยกเลิก", ""],
};
export const statusText = s => `<span class="result ${(STATUS[s] || ["", ""])[1]}">${(STATUS[s] || [s])[0]}</span>`;

// --- the published TV formula (same as market_core.py) so the sheet updates while typing
const STEPS = [[1, "PREMIUM", 1.08], [3, "GRADE_1", 1.04], [5, "GRADE_2", 1.00], [7, "GRADE_3", 0.96], [15, "BELOW_STANDARD", 0.85], [Infinity, "REJECT", 0.60]];
const SCREEN = {12: .92, 13: .97, 14: 1, 15: 1, 16: 1.03, 17: 1.03, 18: 1.06};
export function valuation(m, d, s) {
  if (![m, d, s].every(x => Number.isFinite(x))) return null;
  const [limit, grade, step] = STEPS.find(([lim]) => d <= lim);
  const g = (1 - d / 100) * step;
  const mf = m <= 12.5 ? 1 : m <= 15 ? (1 - m / 100) / 0.875 - 0.02 * (m - 12.5) : 0.60;
  const sc = Math.min(18, Math.max(12, Math.trunc(s)));
  const sf = SCREEN[sc];
  const tv = 120 * g * mf * sf;
  return {grade, step, limit, g, m: mf, s: sf, screen: sc, tv, tv2: Math.round(tv * 100 + 1e-7) / 100};
}
export const passes = (sample, spec) => sample.defect_pct <= spec.max_defect_pct && sample.moisture_pct <= spec.max_moisture_pct &&
  (spec.min_screen == null || sample.screen >= spec.min_screen);
export const num = v => v === "" || v == null ? NaN : Number(v);
