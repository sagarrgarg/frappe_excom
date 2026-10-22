import { useFrappeGetCall } from "@/lib/api";
import { Input, Select } from "../primitives";
import type { FilterOptions } from "./types";
import { useMarketingFilters, type Preset } from "./useMarketingFilters";

const PRESETS: { value: Preset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "mtd", label: "Month to date" },
  { value: "custom", label: "Custom" },
];

export function useFilterOptions() {
  const { data } = useFrappeGetCall<{ message: FilterOptions }>("excom.excom_ads.dashboard.filter_options", undefined, "ads-filter-options", { revalidateOnFocus: false });
  return data?.message;
}

/** One bar drives every widget. Options come from the server and only list what the user may see. */
export function FilterBar({ showDates = true }: { showDates?: boolean }) {
  const { preset, range, filters, set } = useMarketingFilters();
  const opts = useFilterOptions();
  const workspaces = (opts?.workspaces ?? []).filter((w) => !filters.company || w.company === filters.company);
  const brands = (opts?.brands ?? []).filter((b) => !filters.workspace || b.workspace === filters.workspace);
  const accounts = (opts?.accounts ?? []).filter((a) => (!filters.workspace || a.workspace === filters.workspace) && (!filters.provider || a.provider === filters.provider));
  const sel = "h-7 text-xs max-w-[180px]";
  return (
    <div className="flex flex-wrap items-center gap-1.5 min-w-0" role="toolbar" aria-label="Filters">
      {showDates && (
        <>
          <Select className={sel} value={preset} aria-label="Date range" onChange={(e) => set({ range: e.target.value, from: e.target.value === "custom" ? range.from : null, to: e.target.value === "custom" ? range.to : null })}>
            {PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </Select>
          {preset === "custom" && (
            <>
              <Input type="date" className="h-7 text-xs w-[140px]" aria-label="From" value={range.from} max={range.to} onChange={(e) => set({ from: e.target.value })} />
              <Input type="date" className="h-7 text-xs w-[140px]" aria-label="To" value={range.to} min={range.from} onChange={(e) => set({ to: e.target.value })} />
            </>
          )}
        </>
      )}
      {(opts?.companies.length ?? 0) > 1 && (
        <Select className={sel} value={filters.company ?? ""} aria-label="Company" onChange={(e) => set({ company: e.target.value, workspace: null, brand: null, account: null })}>
          <option value="">All companies</option>
          {opts?.companies.map((c) => <option key={c} value={c}>{c}</option>)}
        </Select>
      )}
      {workspaces.length > 1 && (
        <Select className={sel} value={filters.workspace ?? ""} aria-label="Workspace" onChange={(e) => set({ workspace: e.target.value, brand: null, account: null })}>
          <option value="">All workspaces</option>
          {workspaces.map((w) => <option key={w.name} value={w.name}>{w.name}</option>)}
        </Select>
      )}
      {brands.length > 0 && (
        <Select className={sel} value={filters.brand ?? ""} aria-label="Brand" onChange={(e) => set({ brand: e.target.value })}>
          <option value="">All brands</option>
          {brands.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}
        </Select>
      )}
      {(opts?.providers.length ?? 0) > 1 && (
        <Select className={sel} value={filters.provider ?? ""} aria-label="Network" onChange={(e) => set({ provider: e.target.value, account: null })}>
          <option value="">All networks</option>
          {opts?.providers.map((p) => <option key={p} value={p}>{p}</option>)}
        </Select>
      )}
      {accounts.length > 1 && (
        <Select className={sel} value={filters.account ?? ""} aria-label="Ad account" onChange={(e) => set({ account: e.target.value })}>
          <option value="">All ad accounts</option>
          {accounts.map((a) => <option key={a.name} value={a.name}>{a.account_name}</option>)}
        </Select>
      )}
    </div>
  );
}
