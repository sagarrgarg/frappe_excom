/** Formatting for marketing figures. null/undefined → "—" (not available), never "0". */
export const NA = "—";

export function money(v: number | null | undefined, currency?: string | null, compact = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return NA;
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: currency || "INR", maximumFractionDigits: compact || Math.abs(v) >= 1000 ? 0 : 2, notation: compact ? "compact" : "standard" }).format(v);
  } catch {
    return `${currency ?? ""} ${v.toFixed(2)}`.trim();
  }
}

export function num(v: number | null | undefined, compact = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return NA;
  const short = compact && Math.abs(v) >= 10000;
  // Compact numbers keep one decimal (1.5L, 2.3L), or neighbouring axis ticks read the same.
  return new Intl.NumberFormat("en-IN", { notation: short ? "compact" : "standard", maximumFractionDigits: short || !Number.isInteger(v) ? 1 : 0 }).format(v);
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return NA;
  return `${v.toFixed(v >= 10 ? 1 : 2)}%`;
}

export function ratio(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return NA;
  return `${v.toFixed(2)}×`;
}

/** Change vs previous period as a fraction; null when either side is missing or the base is 0. */
export function delta(cur: number | null | undefined, prev: number | null | undefined): number | null {
  if (cur === null || cur === undefined || prev === null || prev === undefined || prev === 0) return null;
  return (cur - prev) / Math.abs(prev);
}

export function ago(ts: string | null | undefined): string {
  if (!ts) return "never";
  const d = new Date(ts.replace(" ", "T"));
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (Number.isNaN(s)) return ts;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
