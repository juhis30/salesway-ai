// Indian number formatting: lakh (1,00,000) and crore (1,00,00,000).

type Num = string | number | null | undefined;

function toNumber(value: Num): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

const fullInr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const plain = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** ₹12,28,363 */
export function inr(value: Num): string {
  const n = toNumber(value);
  return n === null ? "—" : fullInr.format(n);
}

/** ₹1.23 Cr · ₹12.3 L · ₹45.6K · ₹950 */
export function inrCompact(value: Num): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1e7) return `${sign}₹${trim(abs / 1e7, 2)} Cr`;
  if (abs >= 1e5) return `${sign}₹${trim(abs / 1e5, abs >= 1e6 ? 1 : 2)} L`;
  if (abs >= 1e3) return `${sign}₹${trim(abs / 1e3, 1)}K`;
  return `${sign}₹${plain.format(abs)}`;
}

/** Axis ticks: one compact style per axis, no decimals where avoidable. */
export function inrAxis(value: number): string {
  if (value === 0) return "0";
  if (Math.abs(value) >= 1e7) return `${trim(value / 1e7, 1)} Cr`;
  if (Math.abs(value) >= 1e5) return `${trim(value / 1e5, 0)} L`;
  if (Math.abs(value) >= 1e3) return `${trim(value / 1e3, 0)}K`;
  return plain.format(value);
}

function trim(n: number, digits: number): string {
  return n.toFixed(digits).replace(/\.0+$|(\.\d*?)0+$/, "$1");
}

export function number(value: Num): string {
  const n = toNumber(value);
  return n === null ? "—" : plain.format(n);
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}%`;
}

export function signedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(1)}%`;
}

const dayFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const shortDayFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const monthFmt = new Intl.DateTimeFormat("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" });
const longMonthFmt = new Intl.DateTimeFormat("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });

// API dates are plain YYYY-MM-DD; parse as UTC so the browser timezone can't shift the day.
function parse(iso: string): Date {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`);
}

export const fmtDate = (iso: string | null | undefined) => (iso ? dayFmt.format(parse(iso)) : "—");
export const fmtMonth = (iso: string) => monthFmt.format(parse(iso));
export const fmtLongMonth = (iso: string) => longMonthFmt.format(parse(iso));

/** "1 – 31 Oct 2026" or "1 Sep – 31 Oct 2026" */
export function fmtRange(start: string, end: string): string {
  const s = parse(start);
  const e = parse(end);
  if (start === end) return dayFmt.format(e);
  if (s.getUTCFullYear() === e.getUTCFullYear()) {
    if (s.getUTCMonth() === e.getUTCMonth()) return `${s.getUTCDate()} – ${dayFmt.format(e)}`;
    return `${shortDayFmt.format(s)} – ${dayFmt.format(e)}`;
  }
  return `${dayFmt.format(s)} – ${dayFmt.format(e)}`;
}
