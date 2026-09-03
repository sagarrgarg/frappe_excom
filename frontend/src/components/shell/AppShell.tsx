import { Suspense, lazy, useEffect } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Rail } from "./Rail";
import { PhoneTabs } from "./PhoneTabs";
import { useInbox } from "./InboxProvider";
import { useHotkeys } from "../../lib/hotkeys";
import { NewConversationDialog } from "../NewConversationDialog";

const CommandPalette = lazy(() => import("../CommandPalette").then((m) => ({ default: m.CommandPalette })));

/**
 * AppShell — one tree, four widths (UX-001 §3).
 *  phone  : single column + bottom tabs (rail hidden)
 *  tablet : rail + one column
 *  laptop : rail + list + record (details = push drawer)
 *  wide   : rail + list + record + details persistent
 * The rail is absolutely positioned and content is padded by --ex-rail-w so hover-expand never reflows.
 */
export function AppShell() {
  const { bp, newOpen, setNewOpen, paletteOpen, setPaletteOpen, toggleDetails, pendingSelect, refresh, closeRecord } = useInbox();
  const navigate = useNavigate();
  const location = useLocation();
  const phone = bp === "phone";
  const inRecord = location.pathname.startsWith("/t/");

  useHotkeys(
    {
      "mod+k": () => setPaletteOpen(true),
      "mod+n": () => setNewOpen(true),
      "mod+.": () => toggleDetails(),
      "g i": () => navigate("/inbox"),
      "g t": () => navigate("/inbox/today"),
      "g p": () => navigate("/pipeline"),
      "g b": () => navigate("/broadcasts"),
      "g a": () => navigate("/analytics"),
      "g s": () => navigate("/settings"),
      "/": () => document.querySelector<HTMLInputElement>("[data-search-input]")?.focus(),
      Escape: () => { if (inRecord && (bp === "phone" || bp === "tablet")) closeRecord(); },
    },
    [navigate, inRecord, bp]
  );

  useEffect(() => { document.documentElement.dataset.bp = bp; }, [bp]);

  return (
    <div className="h-full w-full flex flex-col bg-surface text-ink-1 overflow-hidden">
      <div className="relative flex-1 min-h-0 flex">
        {!phone && <Rail />}
        <main className={phone ? "flex-1 min-w-0 min-h-0 flex" : "flex-1 min-w-0 min-h-0 flex pl-rail"}>
          <Outlet />
        </main>
      </div>
      {phone && !inRecord && <PhoneTabs />}

      {newOpen && (
        <NewConversationDialog
          onClose={() => setNewOpen(false)}
          onConversationCreated={(threadId, identityName) => {
            setNewOpen(false);
            pendingSelect.current = { id: identityName, via: threadId };
            navigate("/inbox");
            refresh();
          }}
        />
      )}
      {paletteOpen && (
        <Suspense fallback={null}>
          <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
        </Suspense>
      )}
    </div>
  );
}

export function RouteFallback() {
  return (
    <div className="flex-1 flex items-center justify-center text-ink-3">
      <Loader2 className="size-5 animate-spin" />
    </div>
  );
}
