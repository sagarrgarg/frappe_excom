import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { isoDate } from "./format";

/** Every filter lives in the URL, so a view can be bookmarked and shared, and back works. */
export const FILTER_KEYS = ["company", "workspace", "brand", "provider", "account"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];
export type Preset = "today" | "7" | "30" | "90" | "mtd" | "custom";

export function presetRange(p: Preset, today = new Date()): { from: string; to: string } {
  const to = isoDate(today);
  const back = (n: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (n - 1));
    return isoDate(d);
  };
  if (p === "today") return { from: to, to };
  if (p === "mtd") return { from: isoDate(new Date(today.getFullYear(), today.getMonth(), 1)), to };
  return { from: back(p === "custom" ? 30 : Number(p)), to };
}

export function useMarketingFilters() {
  const [params, setParams] = useSearchParams();
  const preset = (params.get("range") as Preset) || "30";
  const range = useMemo(() => {
    if (preset === "custom" && params.get("from") && params.get("to")) return { from: params.get("from") as string, to: params.get("to") as string };
    return presetRange(preset);
  }, [preset, params]);
  const filters = useMemo(() => {
    const f: Partial<Record<FilterKey, string>> = {};
    for (const k of FILTER_KEYS) {
      const v = params.get(k);
      if (v) f[k] = v;
    }
    return f;
  }, [params]);

  const set = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v);
        else next.delete(k);
      }
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  /** Query params for the dashboard API: dates + only the filters that are set. */
  const apiParams = useMemo(() => ({ from_date: range.from, to_date: range.to, ...filters }), [range, filters]);
  return { preset, range, filters, set, apiParams };
}
