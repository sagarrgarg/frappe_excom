import { NavLink, useLocation } from "react-router-dom";
import { Sun, Inbox, KanbanSquare, MoreHorizontal, Plus } from "lucide-react";
import { cn } from "../ui/utils";
import { Badge } from "../primitives";
import { useInbox } from "./InboxProvider";

/** Phone bottom tabs (Today · Inbox · Pipeline · More) + FAB. */
export function PhoneTabs() {
  const { totalUnread, setNewOpen } = useInbox();
  const location = useLocation();
  const inboxActive = location.pathname.startsWith("/inbox") || location.pathname.startsWith("/t/");
  const tabs: { to: string; label: string; icon: React.ReactNode; badge?: number; flagged?: boolean }[] = [
    { to: "/today", label: "Today", icon: <Sun /> },
    { to: "/inbox", label: "Inbox", icon: <Inbox />, badge: totalUnread },
    { to: "/pipeline", label: "Pipeline", icon: <KanbanSquare /> },
    { to: "/more", label: "More", icon: <MoreHorizontal /> },
  ];
  return (
    <>
      <button
        type="button"
        aria-label="New conversation"
        onClick={() => setNewOpen(true)}
        className="fixed right-4 bottom-[calc(64px+env(safe-area-inset-bottom))] z-30 size-12 rounded-full bg-crayon-blue-base text-white shadow-ex flex items-center justify-center"
      >
        <Plus className="size-6" />
      </button>
      <nav className="shrink-0 h-14 border-t border-border bg-surface flex items-stretch safe-area-bottom" aria-label="Sections">
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            onClick={(e) => { if (t.flagged) e.preventDefault(); }}
            className={({ isActive }) =>
              cn(
                "relative flex-1 flex flex-col items-center justify-center gap-0.5 text-xs text-ink-3 [&>svg]:size-5 min-w-0",
                (isActive || (t.to === "/inbox" && inboxActive)) && "text-crayon-blue-text",
                t.flagged && "opacity-40"
              )
            }
          >
            {t.icon}
            <span className="truncate">{t.label}</span>
            {typeof t.badge === "number" && t.badge > 0 && <Badge solid count={t.badge} className="absolute top-1 left-[calc(50%+6px)]" />}
          </NavLink>
        ))}
      </nav>
    </>
  );
}
