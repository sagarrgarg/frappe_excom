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
  /** Manual (imported) accounts: as fresh as their last import, not judged by the sync clock. */
  manual?: { account: string; account_name: string; last_import: string | null }[];
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
  /** ERPNext / CRM results for the same view. Never added to the platform figures above. */
  verified?: VerifiedBlock | null;
  data_quality?: DataQuality | null;
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
  /** Present when the explorer is asked with_verified=1 (Campaign level). */
  verified?: VerifiedFigures | null;
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
  reference_doctype?: string | null;
  reference_name?: string | null;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  snoozed_until: string | null;
  acknowledged_by: string | null;
  escalated?: number;
  escalated_at?: string | null;
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
  analytics_properties?: { name: string; property_name: string }[];
}

/** excom.excom_ads.billing — every figure carries its availability; value is null unless Available or Stale. */
export type Availability = "Not Read" | "Available" | "Not Set" | "Unsupported" | "Permission Missing" | "No Data" | "Stale" | "Error";

export interface Figure<T = number> {
  value: T | null;
  status: Availability;
}

export interface Ledger {
  label: string;
  avail: Availability;
  balance: number | null;
  topups?: number | null;
  refunds?: number | null;
  adjustments?: number | null;
  spend_since?: number | null;
  since?: string | null;
  spend_through?: string | null;
  records: number;
  days_left?: number | null;
  warning?: string | null;
}

export interface AccountBudget {
  id: string | null;
  name: string | null;
  budget_status: string | null;
  start: string | null;
  end: string | null;
  unlimited: boolean;
  approved_limit: number | null;
  adjusted_limit: number | null;
  amount_served: number | null;
  total_adjustments: number | null;
}

export interface BillingDetail extends BillingRow {
  snapshot?: string;
  snapshot_date?: string;
  read_at: string | null;
  read_status: "OK" | "Partial" | "Error" | null;
  last_error: string | null;
  stale: boolean;
  reported: {
    amount_spent: Figure;
    spend_cap: Figure;
    amount_due: Figure;
    is_prepay: Figure<boolean>;
    funding_type: Figure<string>;
    payment_model: Figure<string>;
    billing_setup_status: Figure<string>;
    account_budget: { status: Availability; value: AccountBudget | null };
  } | null;
  calculated: {
    headroom: Figure & { basis: string | null };
    avg_daily_spend: Figure & { days: number };
    days_left: Figure;
    exhaustion_date: Figure<string>;
    note: string | null;
  } | null;
  ledger?: Ledger;
}

export interface BillingResponse {
  accounts: BillingDetail[];
  stale_after_hours: number;
  low_headroom_days: number;
  ledger_label: string;
  can_add_records: boolean;
}

/** excom.excom_ads.budgets — planned values are null where no approved plan line exists. */
export interface PlanVsActualFigures {
  currency: string | null;
  planned_spend: number | null;
  planned_to_date: number | null;
  target_leads: number | null;
  target_purchases: number | null;
  target_revenue: number | null;
  target_cpl: number | null;
  target_roas: number | null;
  actual_spend: number | null;
  spend_unconverted: number | null;
  actual_leads: number | null;
  actual_purchases: number | null;
  actual_revenue: number | null;
  actual_cpl: number | null;
  actual_roas: number | null;
  variance: number | null;
  variance_pct: number | null;
  pacing: number | null;
}

export interface PlanVsActualRow extends PlanVsActualFigures {
  month: string;
  provider: string;
  brand: string;
  lines: number;
}

export interface PlanVsActual {
  from_date: string;
  to_date: string;
  months: string[];
  level: "Account" | "Campaign";
  by_brand: boolean;
  source: "Platform";
  plans: string[];
  rows: PlanVsActualRow[];
  totals: PlanVsActualFigures[];
}

export interface BudgetPlanRow {
  name: string;
  plan_name: string;
  status: "Draft" | "Approved" | "Cancelled";
  docstatus: 0 | 1 | 2;
  company: string;
  workspace: string;
  brand: string | null;
  period_from: string;
  period_to: string;
  currency: string;
  total_planned: number;
  owner: string;
  approved_by: string | null;
  approved_at: string | null;
  approval_override: 0 | 1;
  amended_from: string | null;
  can_approve: boolean;
}

export interface BudgetPlans {
  plans: BudgetPlanRow[];
  can_manage: boolean;
  can_create: boolean;
}

/** excom.excom_ads.verified — null = not available to this user (no ERPNext read permission / no finance right), never 0. */
export interface VerifiedFigures {
  leads: number | null;
  qualified: number | null;
  opportunities: number | null;
  orders: number | null;
  invoices: number | null;
  new_customers: number | null;
  order_value: number | null;
  revenue: number | null;
  gross_revenue: number | null;
  returns: number | null;
  qualified_rate?: number | null;
}

export interface VerifiedTotals extends VerifiedFigures {
  currency: string | null;
  /** Platform-reported spend of the same view, used only for the two labelled ratios. */
  platform_spend: number | null;
  verified_cpl: number | null;
  cost_per_qualified: number | null;
  mer: number | null;
}

export interface VerifiedBlock {
  source: "Verified";
  error?: string;
  from_date?: string;
  to_date?: string;
  available?: { leads: boolean; opportunities: boolean; orders: boolean; revenue: boolean };
  totals?: VerifiedTotals[];
  rules_used?: Record<string, number>;
  unattributed?: (VerifiedFigures & { currency: string | null })[] | null;
  leads_unlinked?: number | null;
  by_campaign?: (VerifiedTotals & { campaign: string; campaign_name: string | null; brand: string | null; provider: string | null })[];
  by_brand?: (VerifiedFigures & { brand: string; currency: string | null })[];
  series?: (VerifiedFigures & { date: string; currency: string | null })[];
}

export interface DataQuality {
  threshold_pct: number;
  min_amount: number;
  stale_after_hours: number;
  /** null = not shown at brand scope (account-level totals are not visible there). */
  spend_mismatch: { account: string; account_name: string | null; days: number; dates: string[]; worst: { date: string; account_spend: number | null; campaign_spend: number | null; gap: number; currency: string | null } }[] | null;
}

/** excom.excom_ads.analytics.web_report — GA4, source "Analytics": never Verified, never platform ad figures. */
export interface WebMetrics {
  currency?: string | null;
  sessions: number;
  total_users: number;
  new_users: number;
  engaged_sessions: number;
  key_events: number;
  transactions: number;
  purchase_revenue: number;
  engagement_rate: number | null;
  key_events_per_session: number | null;
  transactions_per_session: number | null;
  revenue_per_session: number | null;
}

export interface WebProperty {
  name: string;
  property_name: string;
  property_id: string;
  website: string | null;
  currency: string | null;
  time_zone: string | null;
  sync_enabled: number;
  last_success_at: string | null;
  last_status: string | null;
  error_class: string | null;
  last_error: string | null;
  synced_from: string | null;
  synced_until: string | null;
  data_loss_other_row: number;
  subject_to_thresholding: number;
}

export type WebGroup = "source_medium" | "campaign" | "landing_page" | "date";

export interface WebRow extends WebMetrics {
  session_source?: string | null;
  session_medium?: string | null;
  session_campaign_name?: string | null;
  session_campaign_id?: string | null;
  campaign?: string | null;
  campaign_title?: string | null;
  landing_page?: string | null;
  date?: string;
}

export interface WebReport {
  source: "Analytics";
  from_date: string;
  to_date: string;
  group_by: WebGroup;
  properties: WebProperty[];
  totals: WebMetrics[];
  series: (WebMetrics & { date: string })[];
  rows: WebRow[];
  total_rows: number;
  users_note?: string;
}
