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
const WebsiteTab = lazy(() => import("./WebsiteTab").then((m) => ({ default: m.WebsiteTab })));
const LeadsTab = lazy(() => import("./LeadsTab").then((m) => ({ default: m.LeadsTab })));
const ReportsTab = lazy(() => import("./ReportsTab").then((m) => ({ default: m.ReportsTab })));
const ConnectionsTab = lazy(() => import("./ConnectionsTab").then((m) => ({ default: m.ConnectionsTab })));

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "campaigns", label: "Campaigns" },
  { value: "leads", label: "Leads" },
  { value: "website", label: "Website" },
  { value: "budgets", label: "Budgets" },
  { value: "accounts", label: "Accounts" },
  { value: "billing", label: "Billing" },
  { value: "bills", label: "Ad bills" },
  { value: "reports", label: "Reports" },
  { value: "alerts", label: "Alerts" },
  { value: "activity", label: "Activity" },
  { value: "connections", label: "Connections" },
] as const;
type Tab = (typeof TABS)[number]["value"];
const DATED: Tab[] = ["overview", "campaigns", "leads", "website", "budgets"];
// Finance screens are not offered without the view_finance right (the API refuses them too).
const FINANCE: Tab[] = ["billing", "bills"];
// Connection health is for people who run the connections.
const ADMIN: Tab[] = ["connections"];

/** /marketing/:tab — the Marketing OS inside Excom. Filters live in the query string. */
export function MarketingPage() {
  const { tab: raw } = useParams();
  const opts = useFilterOptions();
  const rights = opts?.rights;
  // Website (GA4) only when a property is connected in the user's scope.
  const tabs = TABS.filter((t) => (!FINANCE.includes(t.value) || rights?.view_finance) && (!ADMIN.includes(t.value) || rights?.manage_connections) && (t.value !== "website" || (opts?.analytics_properties?.length ?? 0) > 0));
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
          <div className="min-w-0 max-w-full overflow-x-auto">
            <SegmentedControl<Tab> value={tab} onChange={(t) => navigate(`/marketing/${t}?${params.toString()}`)} segments={tabs.map((t) => ({ value: t.value, label: t.label }))} ariaLabel="Marketing sections" />
          </div>
          <div className="ml-auto min-w-0"><FilterBar showDates={DATED.includes(tab)} /></div>
        </div>
      }
    >
      <Suspense fallback={<RouteFallback />}>
        {tab === "overview" && <OverviewTab />}
        {tab === "campaigns" && <CampaignsTab />}
        {tab === "leads" && <LeadsTab />}
        {tab === "reports" && <ReportsTab />}
        {tab === "website" && <WebsiteTab />}
        {tab === "accounts" && <AccountsTab />}
        {tab === "alerts" && <AlertsTab />}
        {tab === "activity" && <ActivityTab />}
        {tab === "budgets" && <BudgetsTab />}
        {tab === "billing" && <BillingTab />}
        {tab === "bills" && <BillsTab />}
        {tab === "connections" && <ConnectionsTab />}
      </Suspense>
    </AdminPage>
  );
}
