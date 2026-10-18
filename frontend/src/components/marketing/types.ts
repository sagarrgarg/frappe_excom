/** Shapes returned by excom.excom_ads.dashboard. A null metric means "not available", never zero. */
export interface Metrics {
  currency?: string;
  spend: number;
  spend_unconverted: number;
  rows_unconverted: number;
  impressions: number;
  clicks: number;
  link_clicks: number;
  leads: number;
  purchases: number;
  purchase_value: number;
  conversations: number;
  ctr: number | null;
  cpc: number | null;
  cpm: number | null;
  cpl: number | null;
  cost_per_purchase: number | null;
  roas: number | null;
  metrics_synced_at?: string | null;
}

export interface Freshness {
  as_of: string | null;
  stale: { account: string; account_name: string; last_metrics_sync: string | null }[];
  errors: { account: string; account_name: string; error_class: string }[];
  accounts: number;
  stale_after_hours: number;
}

export interface BillingRow {
  account: string;
  account_name: string;
  provider: string;
  currency: string;
  payment_model: string;
  billing_status: string;
  as_of: string | null;
  amount_spent: number | null;
  spend_cap: number | null;
  headroom: number | null;
  amount_due: number | null;
}

export interface Overview {
  from_date: string;
  to_date: string;
  level: "Account" | "Campaign";
  source: "Platform";
  totals: Metrics[];
  previous: { from_date: string; to_date: string; totals: Metrics[] };
  series: (Metrics & { date: string })[];
  by_provider: (Metrics & { provider: string })[];
  by_brand: (Metrics & { brand: string })[];
  top_campaigns: (Metrics & { campaign: string | null; campaign_name: string | null; status: string | null; provider: string; account: string })[];
  freshness: Freshness;
  alerts: Record<string, number>;
  billing: BillingRow[] | null;
}

export interface ExplorerRow {
  name: string;
  title: string;
  status: string;
  effective_status: string | null;
  provider: string;
  account: string;
  brand: string | null;
  external_id: string;
  synced_at: string | null;
  not_returned_since: string | null;
  objective?: string | null;
  daily_budget?: number | null;
  lifetime_budget?: number | null;
  optimization_goal?: string | null;
  creative_name?: string | null;
  currency?: string | null;
  metrics: Metrics | null;
}

export interface Explorer {
  level: "Campaign" | "Ad Group" | "Ad";
  total: number;
  rows: ExplorerRow[];
}

export interface AccountRow {
  name: string;
  account_name: string;
  provider: string;
  external_id: string;
  workspace: string;
  company: string;
  brand: string | null;
  status: string;
  platform_status: string | null;
  disable_reason: string | null;
  currency: string | null;
  timezone_name: string | null;
  payment_model: string;
  sync_enabled: number;
  last_structure_sync: string | null;
  last_metrics_sync: string | null;
  metrics_synced_from: string | null;
  metrics_synced_until: string | null;
  error_class: string | null;
  consecutive_failures: number;
  last_error: string | null;
  billing: BillingRow | null;
  can_manage: boolean;
}

export interface AlertRow {
  name: string;
  title: string;
  alert_type: string;
  severity: "Info" | "Medium" | "High" | "Critical";
  status: "Open" | "Acknowledged" | "Snoozed" | "Resolved";
  message: string | null;
  company: string | null;
  brand: string | null;
  account: string | null;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  snoozed_until: string | null;
  acknowledged_by: string | null;
}

export interface SyncJobRow {
  name: string;
  account: string;
  job_type: string;
  status: "Queued" | "Running" | "Success" | "Partial" | "Failed";
  triggered_by: string | null;
  window_from: string | null;
  window_to: string | null;
  started_at: string | null;
  finished_at: string | null;
  api_calls: number;
  records_written: number;
  error_class: string | null;
  error: string | null;
}

export interface FilterOptions {
  companies: string[];
  workspaces: { name: string; company: string }[];
  brands: { name: string; workspace: string }[];
  accounts: { name: string; account_name: string; provider: string; workspace: string; brand: string | null }[];
  providers: string[];
  rights: Record<string, boolean>;
}
