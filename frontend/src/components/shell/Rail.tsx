import { useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  Plus, Sun, Inbox, KanbanSquare, Users, Radio, BarChart3, Building2, ChevronsUpDown,
  Shield, GitMerge, ListChecks, Cog, Settings, Rows3, LogOut, Bug, Sparkles, ArrowLeftRight, Keyboard, ListTodo, Phone,
  CheckSquare, Megaphone, PanelLeftClose, PanelLeftOpen, Bot,
} from "lucide-react";
import { hasRole } from "../../lib/ui-flag";
import { cn } from "../ui/utils";
import { Avatar, Badge, Menu, menuItemClass, Kbd } from "../primitives";
import { useInbox } from "./InboxProvider";
import { useCompanies } from "../../hooks/useCompanies";
import { useInboxMeta } from "../../hooks/useInboxMeta";
import { useWorklist } from "../../hooks/useWorklist";
import { AttentionBell } from "./AttentionBell";
import { useExcomBranding } from "../../hooks/useBranding";
import { currentUserFullName, currentUserImage } from "../../lib/ui-flag";
import { AvailabilityToggle } from "../voice/AvailabilityToggle";
import { LinePicker } from "../voice/LinePicker";
import { useSoftphoneContext } from "../voice/SoftphoneProvider";
import { MOD } from "../../lib/hotkeys";
import { FeedbackDialog } from "./FeedbackDialog";

interface RailItem {
  to: string;
  label: string;
  icon: React.ReactNode;
  badge?: number;
  flagged?: boolean;
}

/**
 * Rail (UX-001 §3.3) — 56px icons, or 200px with labels when the agent pins it open.
 *
 * It used to widen on hover, as an overlay. That covered the first column of whatever page was
 * open, so a pointer passing on its way to the list opened the rail over the very row being
 * clicked. Now it only changes when asked (the pin button), it remembers, and the page makes room
 * for it instead of being covered. The collapsed rail names each item in its tooltip.
 *
 * Only Inbox carries a number: people waiting for a reply. Tasks, merge suggestions and the rest
 * say their counts on their own pages; a badge on everything means none of them is read.
 */
const PIN_KEY = "excom_rail_pinned";

function readPinned(): boolean {
  try { return localStorage.getItem(PIN_KEY) === "1"; } catch { return false; }
}

function applyRailWidth(pinned: boolean) {
  document.documentElement.style.setProperty("--ex-rail-w", pinned ? "200px" : "56px");
}
const RAIL_EXPLAIN: Record<string, string> = {
  "/inbox": "Every conversation across WhatsApp, email and web chat, one row per contact.",
  "/intake": "Leads: what needs you now (overdue follow-ups, SLA risks, unassigned enquiries), then every lead by stage. Managers see their teams' sources; members see leads assigned to them.",
  "/pipeline": "Open opportunities by stage, per customer type.",
  "/contacts": "Everyone you have talked to, with their ERP record.",
  "/broadcasts": "Template sends to subscriber lists, with delivery logs.",
  "/analytics": "Response times, volumes, funnel.",
  "/marketing": "Ads across Meta and Google: spend, results, campaigns, account health and alerts.",
  "/agents": "AI agents: what each is doing today, what waits for your approval, and the builder for a new one.",
};

export function Rail() {
  const { totalUnread, setNewOpen, filters, setFilters, density, setDensity, setPaletteOpen } = useInbox();
  // Counted on the server over everything, not in the browser over the page it happens to hold.
  const { worklist, ready } = useWorklist();
  const unreadThreads = ready ? worklist.unread_threads : totalUnread;
  const { companies } = useCompanies();
  const { mergeCount } = useInboxMeta();
  const { branding } = useExcomBranding();
  const voiceEnabled = Boolean(useSoftphoneContext()?.config?.enabled);
  const [expanded, setExpanded] = useState(readPinned);
  const [feedback, setFeedback] = useState(false);
  const navigate = useNavigate();
  const isManager = hasRole("Excom Admin") || hasRole("System Manager");
  const location = useLocation();

  useEffect(() => {
    applyRailWidth(expanded);
    try { localStorage.setItem(PIN_KEY, expanded ? "1" : "0"); } catch { /* private window: forget it */ }
  }, [expanded]);
  // Leaving the shell (phone width hides the rail) must not leave the page padded for a rail.
  useEffect(() => () => applyRailWidth(false), []);

  const items: RailItem[] = [
    { to: "/inbox", label: "Inbox", icon: <Inbox />, badge: unreadThreads },
    { to: "/tasks", label: "Tasks", icon: <CheckSquare /> },
    { to: "/intake", label: "Leads", icon: <ListTodo /> },
    { to: "/pipeline", label: "Pipeline", icon: <KanbanSquare /> },
    { to: "/contacts", label: "Contacts", icon: <Users /> },
  ];
  const secondary: RailItem[] = [
    // Only where a voice line exists — an empty Calls page on a site with no telephony is noise.
    ...(voiceEnabled ? [{ to: "/calls", label: "Calls", icon: <Phone /> } as RailItem] : []),
    { to: "/broadcasts", label: "Broadcasts", icon: <Radio /> },
    { to: "/analytics", label: "Analytics", icon: <BarChart3 /> },
    ...(hasRole("Excom Marketing") || isManager ? [{ to: "/marketing", label: "Marketing", icon: <Megaphone /> } as RailItem] : []),
    // The Agents API is for Excom users (excom_ai/builder/api.py _can_see_agents).
    ...(hasRole("Excom Agent") || isManager ? [{ to: "/agents", label: "Agents", icon: <Bot /> } as RailItem] : []),
  ];

  const inboxActive = location.pathname.startsWith("/inbox") || location.pathname.startsWith("/t/");

  const link = (it: RailItem) => (
    <NavLink
      key={it.to}
      to={it.to}
      title={it.flagged ? `${it.label} — available in P3` : expanded ? undefined : `${it.label}${it.badge ? ` · ${it.badge}` : ""}`}
      data-detail={`${it.label} | ${RAIL_EXPLAIN[it.to] || ""}`}
      aria-disabled={it.flagged}
      onClick={(e) => { if (it.flagged) { e.preventDefault(); } }}
      className={({ isActive }) =>
        cn(
          "relative flex items-center h-10 rounded-md mx-1.5 px-2.5 gap-3 text-sm font-medium text-ink-2 hover:bg-surface-hover hover:text-ink-1 min-w-0 [&>svg]:size-5 [&>svg]:shrink-0",
          (isActive || (it.to === "/inbox" && inboxActive)) && "bg-surface-active text-ink-1",
          it.flagged && "opacity-40 cursor-not-allowed"
        )
      }
    >
      {it.icon}
      <span className={cn("truncate transition-opacity", expanded ? "opacity-100" : "opacity-0 w-0")}>{it.label}</span>
      {typeof it.badge === "number" && it.badge > 0 && (
        <Badge solid count={it.badge} className={cn(expanded ? "ml-auto" : "absolute -top-1 right-0 ring-2 ring-surface-sunken")} />
      )}
    </NavLink>
  );

  return (
    <nav
      aria-label="Primary"
      className="absolute inset-y-0 left-0 z-30 flex flex-col w-rail bg-surface-sunken border-r border-border py-2 transition-[width] duration-150 overflow-x-hidden overflow-y-auto"
    >
      {/* Company switcher — hidden with one company */}
      {companies.length > 1 && (
        <Menu.Root modal={false}>
          <Menu.Trigger asChild>
            <button type="button" className="flex items-center h-10 mx-1.5 px-2.5 gap-3 rounded-md text-sm font-medium text-ink-1 hover:bg-surface-hover min-w-0" title={filters.company || "All companies"}>
              <Building2 className="size-5 shrink-0 text-ink-2" />
              <span className={cn("truncate flex-1 text-left", expanded ? "opacity-100" : "opacity-0 w-0")}>{filters.company || "All companies"}</span>
              {expanded && <ChevronsUpDown className="size-4 text-ink-3 shrink-0" />}
            </button>
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Content side="right" align="start" sideOffset={6} className="z-50 min-w-[200px] rounded-lg border border-border bg-surface p-1 shadow-ex">
              <Menu.Item className={menuItemClass} onSelect={() => setFilters({ ...filters, company: "" })}>All companies</Menu.Item>
              {companies.map((c) => (
                <Menu.Item key={c.name} className={menuItemClass} onSelect={() => setFilters({ ...filters, company: c.name })}>
                  <span className="truncate">{c.name}</span>
                </Menu.Item>
              ))}
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>
      )}

      {/* New — single primary action */}
      <button
        type="button"
        onClick={() => setNewOpen(true)}
        title={`New conversation (${MOD}+N)`}
        className="flex items-center h-10 mx-1.5 my-1 px-2.5 gap-3 rounded-md bg-crayon-blue-base text-white text-sm font-medium hover:bg-crayon-blue-text min-w-0"
      >
        <Plus className="size-5 shrink-0" />
        <span className={cn("truncate", expanded ? "opacity-100" : "opacity-0 w-0")}>New</span>
      </button>

      <div className="flex flex-col gap-0.5 mt-1">{items.map(link)}</div>
      <div className="h-px bg-border mx-3 my-2" />
      <div className="flex flex-col gap-0.5">{secondary.map(link)}</div>
      <div className="flex-1" />
      {/* Sits directly above the avatar so an agent sees whether they are on the queue every time
          they reach for their own menu. Renders nothing if the site has no voice line. */}
      <div className="px-1.5"><AttentionBell compact={!expanded} /></div>
      <div className="px-1.5"><LinePicker compact={!expanded} /></div>
      <div className="px-1.5"><AvailabilityToggle compact={!expanded} /></div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        title={expanded ? "Collapse the menu" : "Show labels"}
        aria-label={expanded ? "Collapse the menu" : "Show labels"}
        aria-pressed={expanded}
        className="flex items-center h-9 mx-1.5 px-2.5 gap-3 rounded-md text-sm text-ink-3 hover:bg-surface-hover hover:text-ink-1 min-w-0 [&>svg]:size-5 [&>svg]:shrink-0"
      >
        {expanded ? <PanelLeftClose /> : <PanelLeftOpen />}
        <span className={cn("truncate", expanded ? "opacity-100" : "opacity-0 w-0")}>Collapse</span>
      </button>
      <div className="h-px bg-border mx-3 my-2" />

      {/* Avatar menu: Teams, Merge, Subscribers, Rules, Settings, density, switch UI, sign out */}
      <Menu.Root modal={false}>
        <Menu.Trigger asChild>
          <button type="button" className="flex items-center h-10 mx-1.5 px-2 gap-3 rounded-md hover:bg-surface-hover min-w-0" title="Account & admin">
            <Avatar name={currentUserFullName()} src={currentUserImage()} size={24} />
            <span className={cn("truncate text-sm text-ink-1 text-left flex-1", expanded ? "opacity-100" : "opacity-0 w-0")}>{currentUserFullName()}</span>
          </button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content side="right" align="end" sideOffset={6} className="z-50 min-w-[220px] rounded-lg border border-border bg-surface p-1 shadow-ex">
            <Menu.Label className="px-2 py-1.5 text-xs text-ink-3 truncate">{branding?.app_name || "Excom"} · {currentUserFullName()}</Menu.Label>
            {isManager && <Menu.Item className={menuItemClass} onSelect={() => navigate("/admin")}><Shield />Admin</Menu.Item>}
            <Menu.Item className={menuItemClass} onSelect={() => navigate("/merge")}><GitMerge />Merge suggestions {mergeCount > 0 && <span className="ml-auto text-xs text-ink-3 tabular-nums">{mergeCount}</span>}</Menu.Item>
            <Menu.Item className={menuItemClass} onSelect={() => navigate("/subscribers")}><ListChecks />Subscribers</Menu.Item>
            <Menu.Item className={menuItemClass} onSelect={() => navigate("/rules")}><Cog />Subscriber rules</Menu.Item>
            <Menu.Item className={menuItemClass} onSelect={() => navigate("/settings")}><Settings />Settings</Menu.Item>
            <Menu.Separator className="my-1 h-px bg-border" />
            <Menu.Item className={menuItemClass} onSelect={() => setPaletteOpen(true)}><Sparkles />Command palette <Kbd className="ml-auto">{MOD} K</Kbd></Menu.Item>
            <Menu.Item className={menuItemClass} onSelect={() => navigate("/settings?section=shortcuts")}><Keyboard />Keyboard shortcuts</Menu.Item>
            <Menu.Item className={menuItemClass} onSelect={() => setDensity(density === "compact" ? "comfortable" : "compact")}>
              <Rows3 />Density: {density === "compact" ? "Compact" : "Comfortable"}
            </Menu.Item>
            <Menu.Separator className="my-1 h-px bg-border" />
            <Menu.Item className={menuItemClass} onSelect={() => setFeedback(true)}><ArrowLeftRight />Send feedback…</Menu.Item>
            <Menu.Item className={menuItemClass} onSelect={() => window.open("https://github.com/sagarrgarg/frappe_excom/issues", "_blank")}><Bug />Report issue</Menu.Item>
            <Menu.Item className={menuItemClass} onSelect={() => { window.location.href = "/?cmd=web_logout"; }}><LogOut />Sign out</Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>

      <FeedbackDialog open={feedback} onOpenChange={setFeedback} />
    </nav>
  );
}
