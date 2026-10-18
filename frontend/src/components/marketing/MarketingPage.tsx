import { lazy, Suspense } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Megaphone } from "lucide-react";
import { AdminPage } from "../shell/AdminPage";
import { RouteFallback } from "../shell/AppShell";
import { SegmentedControl } from "../primitives";
import { FilterBar, useFilterOptions } from "./FilterBar";

const OverviewTab = lazy(() => import("./OverviewTab").then((m) => ({ default: m.OverviewTab })));
const CampaignsTab = lazy(() => import("./CampaignsTab").then((m) => ({ default: m.CampaignsTab })));
const AccountsTab = lazy(() => import("./AccountsTab").then((m) => ({ default: m.AccountsTab })));
const AlertsTab = lazy(() => import("./AlertsTab").then((m) => ({ default: m.AlertsTab })));
const ActivityTab = lazy(() => import("./ActivityTab").then((m) => ({ default: m.ActivityTab })));
const BudgetsTab = lazy(() => import("./BudgetsTab").then((m) => ({ default: m.BudgetsTab })));
const BillingTab = lazy(() => import("./BillingTab").then((m) => ({ default: m.BillingTab })));
const BillsTab = lazy(() => import("./BillsTab").then((m) => ({ default: m.BillsTab })));

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "campaigns", label: "Campaigns" },
  { value: "budgets", label: "Budgets" },
  { value: "accounts", label: "Accounts" },
  { value: "billing", label: "Billing" },
  { value: "bills", label: "Ad bills" },
  { value: "alerts", label: "Alerts" },
  { value: "activity", label: "Activity" },
] as const;
type Tab = (typeof TABS)[number]["value"];
const DATED: Tab[] = ["overview", "campaigns", "budgets"];
// Finance screens are not offered without the view_finance right (the API refuses them too).
const FINANCE: Tab[] = ["billing", "bills"];

/** /marketing/:tab — the Marketing OS inside Excom. Filters live in the query string. */
export function MarketingPage() {
  const { tab: raw } = useParams();
  const rights = useFilterOptions()?.rights;
  const tabs = TABS.filter((t) => !FINANCE.includes(t.value) || rights?.view_finance);
  const tab: Tab = (tabs.find((t) => t.value === raw)?.value ?? "overview") as Tab;
  const navigate = useNavigate();
  const [params] = useSearchParams();
  return (
    <AdminPage
      embedded
      wide
      title="Marketing"
      icon={<Megaphone />}
      toolbar={
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <SegmentedControl<Tab> value={tab} onChange={(t) => navigate(`/marketing/${t}?${params.toString()}`)} segments={tabs.map((t) => ({ value: t.value, label: t.label }))} ariaLabel="Marketing sections" />
          <div className="ml-auto min-w-0"><FilterBar showDates={DATED.includes(tab)} /></div>
        </div>
      }
    >
      <Suspense fallback={<RouteFallback />}>
        {tab === "overview" && <OverviewTab />}
        {tab === "campaigns" && <CampaignsTab />}
        {tab === "accounts" && <AccountsTab />}
        {tab === "alerts" && <AlertsTab />}
        {tab === "activity" && <ActivityTab />}
        {tab === "budgets" && <BudgetsTab />}
        {tab === "billing" && <BillingTab />}
        {tab === "bills" && <BillsTab />}
      </Suspense>
    </AdminPage>
  );
}
