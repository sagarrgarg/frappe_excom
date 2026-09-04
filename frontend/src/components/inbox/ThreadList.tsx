import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { Inbox as InboxIcon, Loader2, PanelLeftClose } from "lucide-react";
import { SearchBar } from "./SearchBar";
import { ViewList } from "./ViewList";
import { ThreadRow, type RowActions } from "./ThreadRow";
import { EmptyState, Button } from "../primitives";
import { useInbox } from "../shell/InboxProvider";
import { useHotkeys } from "../../lib/hotkeys";
import { hasRole } from "../../lib/ui-flag";
import type { UnifiedContact } from "../../types";
import { cn } from "../ui/utils";

/**
 * List column (UX-001 §3.4). Search → view → rows. j/k move focus, ⏎ opens, e archives, a assigns.
 * Swipe-to-archive on touch (left swipe past 96px).
 */
export function ThreadList({ className }: { className?: string }) {
  const { contacts, isLoading, refresh, selectedId, openRecord, closeRecord, coarse, setNewOpen, listError, toggleList, bp } = useInbox();
  const listRef = useRef<HTMLDivElement>(null);
  const [cursor, setCursor] = useState<number>(-1);

  const { call: spamCall } = useFrappePostCall("excom.excom.api.chat.mark_spam");
  const { call: archiveCall } = useFrappePostCall("excom.excom.api.chat.archive_thread");
  const { call: unarchiveCall } = useFrappePostCall("excom.excom.api.chat.unarchive_thread");
  const { call: deleteCall } = useFrappePostCall("excom.excom.api.chat.delete_thread");
  const { call: markReadCall } = useFrappePostCall("excom.excom.api.chat.mark_read");
  const { call: markUnreadCall } = useFrappePostCall("excom.excom.api.chat.mark_unread");
  const { call: assignCall } = useFrappePostCall("excom.excom.api.chat.assign_thread");
  const isSystemManager = hasRole("System Manager");

  const forEachThread = useCallback(async (c: UnifiedContact, fn: (id: string) => Promise<unknown>) => {
    await Promise.all(c.allAccounts.map((a) => fn(a.id)));
  }, []);

  const actions: RowActions = useMemo(() => ({
    archive: async (c) => {
      try {
        await forEachThread(c, (id) => archiveCall({ thread_id: id }));
        if (selectedId === c.id) closeRecord();
        refresh();
        toast.success(`Archived ${c.contactName}`, { duration: 6000, action: { label: "Undo", onClick: async () => { try { await forEachThread(c, (id) => unarchiveCall({ thread_id: id })); refresh(); toast.success("Restored"); } catch { toast.error("Could not restore"); } } } });
      } catch { toast.error("Failed to archive"); }
    },
    unarchive: async (c) => {
      try { await forEachThread(c, (id) => unarchiveCall({ thread_id: id })); toast.success("Reopened — back in the inbox"); refresh(); }
      catch { toast.error("Failed to unarchive"); }
    },
    assignToMe: async (c) => {
      try { await forEachThread(c, (id) => assignCall({ thread_id: id })); toast.success("Assigned to you"); refresh(); }
      catch { toast.error("Failed to assign"); }
    },
    toggleRead: async (c) => {
      const unread = c.totalUnreadCount > 0;
      try { await forEachThread(c, (id) => (unread ? markReadCall({ thread_id: id }) : markUnreadCall({ thread_id: id }))); refresh(); }
      catch { toast.error("Failed to update"); }
    },
    snooze: async (c) => {
      try { await forEachThread(c, (id) => markUnreadCall({ thread_id: id })); toast.success("Marked unread — it will stay in Unread until you open it"); refresh(); }
      catch { toast.error("Failed"); }
    },
    spam: async (c) => {
      try { await forEachThread(c, (id) => spamCall({ thread_id: id })); toast.success("Marked as spam"); if (selectedId === c.id) closeRecord(); refresh(); }
      catch { toast.error("Failed to mark spam"); }
    },
    del: async (c) => {
      try { await forEachThread(c, (id) => deleteCall({ thread_id: id })); toast.success("Deleted"); if (selectedId === c.id) closeRecord(); refresh(); }
      catch { toast.error("Failed to delete"); }
    },
    copy: (c) => {
      const text = c.contactInfo.phone || c.contactInfo.email || c.contactName;
      navigator.clipboard?.writeText(text).then(() => toast.success("Copied")).catch(() => {});
    },
  }), [archiveCall, unarchiveCall, assignCall, markReadCall, markUnreadCall, spamCall, deleteCall, forEachThread, refresh, selectedId, closeRecord]);

  // Keep the keyboard cursor on the selected row.
  useEffect(() => {
    const idx = contacts.findIndex((c) => c.id === selectedId);
    if (idx >= 0) setCursor(idx);
  }, [selectedId, contacts]);

  useEffect(() => {
    if (cursor < 0) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-thread-row][data-id="${CSS.escape(contacts[cursor]?.id || "")}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [cursor, contacts]);

  useHotkeys(
    {
      j: () => setCursor((i) => Math.min(contacts.length - 1, i + 1)),
      k: () => setCursor((i) => Math.max(0, i - 1)),
      ArrowDown: () => setCursor((i) => Math.min(contacts.length - 1, i + 1)),
      ArrowUp: () => setCursor((i) => Math.max(0, i - 1)),
      Enter: () => { const c = contacts[cursor]; if (c) openRecord(c.id); },
      e: () => { const c = contacts[cursor]; if (!c) return; if (c.threads?.[0]?.status === "Closed") actions.unarchive(c); else actions.archive(c); },
      a: () => { const c = contacts[cursor]; if (c) actions.assignToMe(c); },
    },
    [contacts, cursor, actions, openRecord]
  );

  // Touch: swipe left to archive
  const touch = useRef<{ x: number; id: string } | null>(null);

  return (
    <section aria-label="Conversations" className={cn("flex flex-col min-h-0 min-w-0 bg-surface-sunken", className)}>
      <div className="flex items-stretch min-w-0">
        <div className="flex-1 min-w-0"><SearchBar /></div>
        {bp !== "phone" && bp !== "tablet" && <button type="button" onClick={toggleList} title="Hide conversations" aria-label="Hide conversations" className="w-7 shrink-0 border-b border-border text-ink-3 hover:text-ink-1 flex items-center justify-center"><PanelLeftClose className="size-4" /></button>}
      </div>
      <ViewList />
      <div
        ref={listRef}
        className="flex-1 min-h-0 overflow-y-auto overscroll-contain bg-surface"
        onTouchStart={(e) => { const row = (e.target as HTMLElement).closest<HTMLElement>("[data-thread-row]"); if (row) touch.current = { x: e.touches[0].clientX, id: row.dataset.id || "" }; }}
        onTouchEnd={(e) => {
          if (!touch.current) return;
          const dx = e.changedTouches[0].clientX - touch.current.x;
          const c = contacts.find((x) => x.id === touch.current!.id);
          touch.current = null;
          if (c && dx < -96) actions.archive(c);
          if (c && dx > 96) actions.toggleRead(c);
        }}
      >
        {isLoading && contacts.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
        ) : contacts.length === 0 && listError ? (
          <EmptyState icon={<InboxIcon />} title="Couldn't load conversations" hint={listError} action={<Button size="sm" variant="primary" onClick={() => refresh()}>Retry</Button>} />
        ) : contacts.length === 0 ? (
          <EmptyState icon={<InboxIcon />} title="Nothing here" hint="Try another view, clear filters, or start a conversation." action={<Button size="sm" variant="primary" onClick={() => setNewOpen(true)}>New conversation</Button>} />
        ) : (
          contacts.map((c, i) => (
            <div key={c.id} className={cn(i === cursor && c.id !== selectedId && "ring-1 ring-inset ring-crayon-blue-base/50")}>
              <ThreadRow c={c} selected={c.id === selectedId} onOpen={() => openRecord(c.id)} actions={actions} coarse={coarse} isSystemManager={isSystemManager} />
            </div>
          ))
        )}
      </div>
    </section>
  );
}
