/**
 * Recharts needs literal colour strings. Read them from the token CSS variables at runtime so
 * components never carry raw hex (W13 gate) and charts follow the crayon palette (UX-001 §9.1).
 */
function v(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function chartTheme() {
  const series = ["blue", "green", "violet", "amber", "rose", "teal", "plum", "sand"].map((k) => v(`--ex-${k}-base`));
  return {
    series,
    byName: { blue: series[0], green: series[1], violet: series[2], amber: series[3], rose: series[4], teal: series[5], plum: series[6], sand: series[7] },
    grid: v("--ex-border"),
    axis: v("--ex-ink-3"),
    ink: v("--ex-ink-1"),
    surface: v("--ex-surface"),
    border: v("--ex-border-strong"),
    tooltip: { background: v("--ex-surface"), border: `1px solid ${v("--ex-border-strong")}`, borderRadius: 8, color: v("--ex-ink-1"), boxShadow: v("--ex-shadow"), fontSize: 12 },
    tick: { fill: v("--ex-ink-3"), fontSize: 12 },
    strokeWidth: 2,
  };
}
