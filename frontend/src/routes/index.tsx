import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell, RouteFallback } from "../components/shell/AppShell";
import { InboxProvider } from "../components/shell/InboxProvider";
import { InboxRoute } from "./inbox";
import { appBasename } from "../lib/ui-flag";
import { FlaggedRoute } from "./flagged";
const TodayRoute = lazy(() => import("./crm").then((m) => ({ default: m.TodayRoute })));
const IntakeRoute = lazy(() => import("./crm").then((m) => ({ default: m.IntakeRoute })));
const PipelineRoute = lazy(() => import("./crm").then((m) => ({ default: m.PipelineRoute })));

const BroadcastsRoute = lazy(() => import("./pages").then((m) => ({ default: m.BroadcastsRoute })));
const AnalyticsRoute = lazy(() => import("./pages").then((m) => ({ default: m.AnalyticsRoute })));
const AdminRoute = lazy(() => import("../components/admin/AdminLayout").then((m) => ({ default: m.AdminLayout })));
const MergeRoute = lazy(() => import("./pages").then((m) => ({ default: m.MergeRoute })));
const SubscribersRoute = lazy(() => import("./pages").then((m) => ({ default: m.SubscribersRoute })));
const RulesRoute = lazy(() => import("./pages").then((m) => ({ default: m.RulesRoute })));
const SettingsRoute = lazy(() => import("./pages").then((m) => ({ default: m.SettingsRoute })));
const ContactsRoute = lazy(() => import("./pages").then((m) => ({ default: m.ContactsRoute })));
const MoreRoute = lazy(() => import("./pages").then((m) => ({ default: m.MoreRoute })));
const StressRoute = lazy(() => import("./stress").then((m) => ({ default: m.StressRoute })));

const L = (el: React.ReactNode) => <Suspense fallback={<RouteFallback />}>{el}</Suspense>;

/**
 * Router (P1 W3). Every filter combination is a URL; back works on phone list → thread → details.
 *   /inbox/:view?     ?channel=&account=&team=&tag=&from=&to=&q=&broadcast=&bstatus=&company=
 *   /t/:recordId      ?view=&tab=chat|tasks|notes|activity&panel=details&via=<threadId>
 *   /today /pipeline /intake   (P3 — registered, flagged)
 *   /broadcasts /analytics /teams /merge /subscribers /rules /settings /contacts /more
 *   /dev/stress
 */
export function NextRouter() {
  return (
    <BrowserRouter basename={appBasename()}>
      <Routes>
        <Route element={<InboxProvider><AppShell /></InboxProvider>}>
          <Route index element={<Navigate to="/inbox" replace />} />
          <Route path="/inbox/:view?" element={<InboxRoute />} />
          <Route path="/t/:recordId" element={<InboxRoute />} />
          <Route path="/today" element={L(<TodayRoute />)} />
          <Route path="/pipeline" element={L(<PipelineRoute />)} />
          <Route path="/intake" element={L(<IntakeRoute />)} />
          <Route path="/p3" element={<FlaggedRoute name="P3 placeholder" />} />
          <Route path="/contacts" element={L(<ContactsRoute />)} />
          <Route path="/broadcasts" element={L(<BroadcastsRoute />)} />
          <Route path="/analytics" element={L(<AnalyticsRoute />)} />
          <Route path="/teams" element={<Navigate to="/admin/teams" replace />} />
          <Route path="/admin/:section?" element={L(<AdminRoute />)} />
          <Route path="/merge" element={L(<MergeRoute />)} />
          <Route path="/subscribers" element={L(<SubscribersRoute />)} />
          <Route path="/rules" element={L(<RulesRoute />)} />
          <Route path="/settings" element={L(<SettingsRoute />)} />
          <Route path="/more" element={L(<MoreRoute />)} />
          <Route path="/dev/stress" element={L(<StressRoute />)} />
          <Route path="*" element={<Navigate to="/inbox" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
