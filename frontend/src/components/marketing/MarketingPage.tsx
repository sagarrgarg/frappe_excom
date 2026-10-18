import { lazy, Suspense } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Megaphone } from "lucide-react";
import { AdminPage } from "../shell/AdminPage";
import { RouteFallback } from "../shell/AppShell";
import { SegmentedControl } from "../primitives";
import { FilterBar } from "./FilterBar";

const OverviewTab = lazy(() => import("./OverviewTab").then((m) => ({ default: m.OverviewTab })));
const CampaignsTab = lazy(() => import("./CampaignsTab").then((m) => ({ default: m.CampaignsTab })));
const AccountsTab = lazy(() => import("./AccountsTab").then((m) => ({ default: m.AccountsTab })));
const AlertsTab = lazy(() => import("./AlertsTab").then((m) => ({ default: m.AlertsTab })));
const ActivityTab = lazy(() => import("./ActivityTab").then((m) => ({ default: m.ActivityTab })));

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "campaigns", label: "Campaigns" },
  { value: "accounts", label: "Accounts" },
  { value: "alerts", label: "Alerts" },
  { value: "activity", label: "Activity" },
] as const;
type Tab = (typeof TABS)[number]["value"];
const DATED: Tab[] = ["overview", "campaigns"];

/** /marketing/:tab — the Marketing OS inside Excom. Filters live in the query string. */
export function MarketingPage() {
  const { tab: raw } = useParams();
  const tab: Tab = (TABS.find((t) => t.value === raw)?.value ?? "overview") as Tab;
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
          <SegmentedControl<Tab> value={tab} onChange={(t) => navigate(`/marketing/${t}?${params.toString()}`)} segments={TABS.map((t) => ({ value: t.value, label: t.label }))} ariaLabel="Marketing sections" />
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
      </Suspense>
    </AdminPage>
  );
}
