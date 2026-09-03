import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { MessageSquare, ListTodo, StickyNote, History, Lock, ArrowRightLeft, Tag, Sparkles, ExternalLink, AlertOctagon, Archive, ArchiveRestore, Trash2, Copy, Eye, EyeOff, UserPlus, PanelRight, Loader2 } from "lucide-react";
import { RecordHeader } from "./RecordHeader";
import { ContextStrip } from "./ContextStrip";
import { MessageFeed } from "./MessageFeed";
import { Composer, type EmailDraft } from "./Composer";
import { TasksTab } from "./TasksTab";
import { NotesTab } from "./NotesTab";
import { ActivityTab } from "./ActivityTab";
import { TransferDialog } from "./TransferDialog";
import { AIAssistantDrawer } from "../AIAssistantDrawer";
import { DetailsTab } from "../crm/DetailsTab";
import { useIdentityRecords, useCrmActions, useCrmOptions } from "../../hooks/useCrm";
import { UserPlus2, ArrowUpRight, Tags } from "lucide-react";
import { SegmentedControl, EmptyState, Button, Modal, type MenuGroup } from "../primitives";
import { TagManager } from "../TagManager";
import { useInbox } from "../shell/InboxProvider";
import { useIdentityMessages, type FeedMessage } from "../../hooks/useIdentityMessages";
import { useRecordRef, deskUrl } from "../../hooks/useRecordLinks";
import { useTasks } from "../../hooks/useTasks";
import { useFileUpload } from "../../hooks/useFileUpload";
import { useThreads } from "../../hooks/useContacts";
import { hasRole } from "../../lib/ui-flag";
import type { Account } from "../../types";

type Tab = "chat" | "tasks" | "notes" | "activity" | "ai" | "details";

/**
 * RecordPane — header (48) + context strip (28) + tabs (36) = 112px of chrome, then the merged feed + composer.
 * Chat / Tasks / Notes / Activity / AI are tabs here at every width; the side pane holds only the contact
 * profile. Details tab is present but flagged off until P3.
 */
export function RecordPane() {
  const { bp, selected, selectedId, closeRecord, refresh: refreshThreads, toggleDetails, setDetailsOpen } = useInbox();
  const [sp, setSp] = useSearchParams();
  const tab = ((sp.get("tab") as Tab) || "chat");
  const setTab = (t: Tab) => { const n = new URLSearchParams(sp); if (t === "chat") n.delete("tab"); else n.set("tab", t); setSp(n, { replace: true }); };

  // Deep link fallback: if the record isn't in the filtered list, try an unfiltered fetch once.
  const { unifiedContacts: fallback, isLoading: fallbackLoading } = useThreads("", "", "", "", "", "", "", "");
  const { unifiedContacts: fallbackArchived } = useThreads("", "", "", "", "", "", "", "", true);
  const contact = selected || fallback.find((c) => c.id === selectedId) || fallbackArchived.find((c) => c.id === selectedId) || null;

  if (!contact) {
    return (
      <div className="flex-1 flex items-center justify-center bg-surface">
        {fallbackLoading ? <Loader2 className="size-5 animate-spin text-ink-3" /> : (
          <EmptyState icon={<MessageSquare />} title="Conversation not in your inbox" hint="It may be closed, marked spam, or outside your teams." action={<Button size="sm" onClick={closeRecord}>Back to inbox</Button>} />
        )}
      </div>
    );
  }
  return <RecordBody key={contact.id} contact={contact} tab={tab} setTab={setTab} bp={bp} closeRecord={closeRecord} refreshThreads={refreshThreads} toggleDetails={toggleDetails} setDetailsOpen={setDetailsOpen} sp={sp} setSp={setSp} />;
}

function RecordBody({ contact, tab, setTab, bp, closeRecord, refreshThreads, toggleDetails, setDetailsOpen, sp, setSp }: any) {
  const wide = bp === "wide";
    const drill = bp === "phone" || bp === "tablet";
  const { messages, isLoading, refresh, autoClaimed, error: feedError, failedThreads, canLoadOlder, loadOlder, loadingOlder } = useIdentityMessages(contact);
  const { record } = useRecordRef(contact.id, contact.contactName);
  const { open: openTasks } = useTasks(record);
  const { records: crmRecords, primary: crmPrimary, refresh: refreshCrm } = useIdentityRecords(contact.id);
  const crmOpts = useCrmOptions();
  const crm = useCrmActions(() => { refreshCrm(); refreshThreads(); });
  const [classifyOpen, setClassifyOpen] = useState(false);
  const [classifyType, setClassifyType] = useState("");
  const threadIds = useMemo(() => contact.allAccounts.map((a: Account) => a.id), [contact.allAccounts]);

  // Reply via: ?via=<threadId> → last inbound message's thread → first account.
  const viaParam = sp.get("via");
  const defaultVia = useMemo(() => {
    const fromParam = contact.allAccounts.find((a: Account) => a.id === viaParam);
    if (fromParam) return fromParam;
    const lastInbound = [...messages].reverse().find((m) => m.sender === "contact");
    return contact.allAccounts.find((a: Account) => a.id === lastInbound?.threadId) || contact.allAccounts[0] || null;
  }, [contact.allAccounts, viaParam, messages]);
  const [via, setViaState] = useState<Account | null>(defaultVia);
  useEffect(() => { if (!viaParam && !messages.length) setViaState(defaultVia); }, [defaultVia, viaParam, messages.length]);
  useEffect(() => { setViaState(defaultVia); /* once messages arrive */ // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading]);
  const setVia = (a: Account) => { setViaState(a); const n = new URLSearchParams(sp); n.set("via", a.id); setSp(n, { replace: true }); };

  const [channelFilter, setChannelFilter] = useState("");
  const [pendingText, setPendingText] = useState<string | null>(null);
  const [replyingTo, setReplyingTo] = useState<FeedMessage | null>(null);
  const [emailDraft, setEmailDraft] = useState<EmailDraft | null>(null);
  const [optimistic, setOptimistic] = useState<{ id: string; content: string; timestamp: Date }[]>([]);
  const [transferOpen, setTransferOpen] = useState(false);
  const [tagsOpen, setTagsOpen] = useState(false);

  const { call: sendMessage } = useFrappePostCall("excom.excom.api.chat.send_message");
  const { call: spamCall } = useFrappePostCall("excom.excom.api.chat.mark_spam");
  const { call: archiveCall } = useFrappePostCall("excom.excom.api.chat.archive_thread");
  const { call: unarchiveCall } = useFrappePostCall("excom.excom.api.chat.unarchive_thread");
  const archived = contact.threads?.[0]?.status === "Closed";
  const { call: deleteCall } = useFrappePostCall("excom.excom.api.chat.delete_thread");
  const { call: markRead } = useFrappePostCall("excom.excom.api.chat.mark_read");
  const { call: markUnread } = useFrappePostCall("excom.excom.api.chat.mark_unread");
  const { call: assignCall } = useFrappePostCall("excom.excom.api.chat.assign_thread");

  const claimedOnce = useRef(false);
  useEffect(() => {
    if (autoClaimed.length && !claimedOnce.current) { claimedOnce.current = true; toast.success("Assigned to you", { duration: 2500 }); refreshThreads(); }
  }, [autoClaimed, refreshThreads]);

  const onSent = useCallback(() => { refresh(); refreshThreads(); }, [refresh, refreshThreads]);
  const onOptimistic = useCallback((o: { id: string; content: string; timestamp: Date } | null, remove?: string) => {
    setOptimistic((prev) => { let next = prev; if (remove) next = next.filter((x) => x.id !== remove); if (o) next = [...next, o]; return next; });
  }, []);

  const fileUpload = useFileUpload(useCallback(async (fileUrl: string, messageType: string) => {
    if (!via) return;
    try { await sendMessage({ thread_id: via.id, message: "", message_type: messageType, media_url: fileUrl }); onSent(); }
    catch { toast.error("Failed to send attachment"); }
  }, [via, sendMessage, onSent]));

  const onReplyEmail = useCallback((gmailId: string, subject: string, to: string, threadId: string) => {
    const acc = contact.allAccounts.find((a: Account) => a.id === threadId);
    if (acc) setVia(acc);
    setEmailDraft({ to, subject, cc: "", inReplyToGmailId: gmailId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contact.allAccounts]);

  const all = (fn: (id: string) => Promise<unknown>) => Promise.all(threadIds.map(fn));
  const unread = contact.totalUnreadCount > 0;
  const menuGroups: MenuGroup[] = [
    [
      { id: "transfer", label: "Transfer…", icon: <ArrowRightLeft />, onSelect: () => setTransferOpen(true) },
      { id: "tags", label: "Tags…", icon: <Tag />, onSelect: () => setTagsOpen(true) },
      { id: "assign", label: "Assign to me", icon: <UserPlus />, shortcut: "a", onSelect: async () => { try { await all((id) => assignCall({ thread_id: id })); toast.success("Assigned to you"); refreshThreads(); } catch { toast.error("Failed"); } } },
      { id: "ai", label: "AI assist", icon: <Sparkles />, onSelect: () => setTab("ai") },
      { id: "details", label: "Details", icon: <PanelRight />, shortcut: "⌘.", onSelect: () => setDetailsOpen(true) },
    ],
    [
      ...(!crmPrimary ? [{ id: "promote", label: "Promote to Lead", icon: <UserPlus2 />, onSelect: async () => { const r = await crm.promoteThread(via?.id || contact.activeAccountId); if (r) setTab("details"); } }] : []),
      ...(crmPrimary?.doctype === "Lead" ? [{ id: "classify", label: crmPrimary.customer_type ? `Classify (${crmPrimary.customer_type})` : "Classify lead…", icon: <Tags />, onSelect: () => setClassifyOpen(true) }] : []),
      ...(crmPrimary?.doctype === "Lead" && crmPrimary.customer_type ? [{ id: "convert", label: "Convert to Opportunity", icon: <ArrowUpRight />, onSelect: async () => { await crm.convert(crmPrimary, "Opportunity"); setTab("details"); } }] : []),
      ...(crmPrimary?.doctype === "Lead" && crmPrimary.customer_type === "Online B2C" ? [{ id: "tocust", label: "Convert to Customer", icon: <ArrowUpRight />, onSelect: async () => { await crm.convert(crmPrimary, "Customer"); } }] : []),
      ...(crmPrimary?.doctype === "Opportunity" ? [{ id: "quote", label: "Create Quotation (Desk)", icon: <ArrowUpRight />, onSelect: async () => { const r: any = await crm.convert(crmPrimary, "Quotation"); if (r?.message?.ref) window.open(deskUrl(r.message.ref.doctype, r.message.ref.name), "_blank"); } }] : []),
    ],
    [
      { id: "read", label: unread ? "Mark as read" : "Mark as unread", icon: unread ? <EyeOff /> : <Eye />, onSelect: async () => { await all((id) => (unread ? markRead : markUnread)({ thread_id: id })); refreshThreads(); } },
      { id: "copy", label: "Copy contact", icon: <Copy />, onSelect: () => navigator.clipboard?.writeText(contact.contactInfo.phone || contact.contactInfo.email || contact.contactName).then(() => toast.success("Copied")) },
      { id: "desk", label: "Open in Desk", icon: <ExternalLink />, onSelect: () => window.open(record ? deskUrl(record.doctype, record.name) : deskUrl("Omni Identity", contact.id), "_blank") },
    ],
    [
      archived
        ? { id: "unarchive", label: "Unarchive", icon: <ArchiveRestore />, onSelect: async () => { try { await all((id) => unarchiveCall({ thread_id: id })); toast.success("Reopened — back in the inbox"); refreshThreads(); } catch { toast.error("Failed"); } } }
        : { id: "archive", label: "Archive", icon: <Archive />, shortcut: "e", onSelect: async () => { try { await all((id) => archiveCall({ thread_id: id })); toast.success("Archived"); closeRecord(); refreshThreads(); } catch { toast.error("Failed"); } } },
      { id: "spam", label: "Mark as spam", icon: <AlertOctagon />, danger: true, onSelect: async () => { try { await all((id) => spamCall({ thread_id: id })); toast.success("Marked as spam"); closeRecord(); refreshThreads(); } catch { toast.error("Failed"); } } },
      ...(hasRole("System Manager") ? [{ id: "delete", label: "Delete", icon: <Trash2 />, danger: true, onSelect: async () => { if (!window.confirm("Delete this conversation and all its threads?")) return; try { await all((id) => deleteCall({ thread_id: id })); toast.success("Deleted"); closeRecord(); refreshThreads(); } catch { toast.error("Failed"); } } }] : []),
    ],
  ];

  const segments = [
    { value: "chat" as Tab, label: "Chat", icon: <MessageSquare /> },
    { value: "tasks" as Tab, label: "Tasks", icon: <ListTodo />, count: openTasks.length },
    { value: "notes" as Tab, label: "Notes", icon: <StickyNote /> },
    { value: "activity" as Tab, label: "Activity", icon: <History /> },
    { value: "ai" as Tab, label: "AI", icon: <Sparkles /> },
    { value: "details" as Tab, label: "Details", icon: crmPrimary ? <Tags /> : <Lock />, disabled: false, hint: crmPrimary ? `${crmPrimary.doctype} ${crmPrimary.name}` : "No CRM record yet — Promote to Lead from ⋯" },
  ];
  const effectiveTab: Tab = tab;

  return (
    <section aria-label={`Conversation with ${contact.contactName}`} className="flex-1 min-w-0 min-h-0 flex flex-col bg-surface">
      <RecordHeader contact={contact} record={record} showBack={drill} onBack={closeRecord} menuGroups={menuGroups} onToggleDetails={toggleDetails} detailsToggleVisible={!wide} />
      <ContextStrip contact={contact} record={record} crm={crmPrimary} onStage={crmPrimary?.doctype === "Opportunity" ? () => setTab("details") : undefined} />
      <SegmentedControl<Tab> ariaLabel="Record sections" className="px-2 border-b border-border bg-surface" value={effectiveTab} onChange={setTab} segments={segments} />

      {effectiveTab === "chat" && (
        <>
          <MessageFeed
            contact={contact}
            messages={messages}
            isLoading={isLoading}
            refresh={refresh}
            optimistic={optimistic}
            onReply={(m) => setReplyingTo(m)}
            onReplyEmail={onReplyEmail}
            channelFilter={channelFilter}
            setChannelFilter={setChannelFilter}
            viaThreadId={via?.id || null}
            error={feedError}
            failedThreads={failedThreads}
            canLoadOlder={canLoadOlder}
            loadOlder={loadOlder}
            loadingOlder={loadingOlder}
            isDragging={fileUpload.isDragging}
            dragHandlers={{ onDragOver: fileUpload.handleDragOver, onDragLeave: fileUpload.handleDragLeave, onDrop: fileUpload.handleDrop }}
          />
          <Composer contact={contact} via={via} setVia={setVia} prefill={pendingText} onPrefillConsumed={() => setPendingText(null)} replyingTo={replyingTo} clearReply={() => setReplyingTo(null)} emailDraft={emailDraft} setEmailDraft={setEmailDraft} onSent={onSent} onOptimistic={onOptimistic} fileUpload={fileUpload} />
        </>
      )}
      {effectiveTab === "tasks" && <div className="flex-1 min-h-0"><TasksTab record={record} /></div>}
      {effectiveTab === "notes" && <div className="flex-1 min-h-0"><NotesTab record={record} /></div>}
      {effectiveTab === "activity" && <div className="flex-1 min-h-0 overflow-y-auto"><ActivityTab record={record} threadIds={threadIds} messages={messages} /></div>}
      {effectiveTab === "details" && <div className="flex-1 min-h-0"><DetailsTab refr={crmPrimary ? { doctype: crmPrimary.doctype, name: crmPrimary.name } : null} onChanged={refreshCrm} /></div>}
      {effectiveTab === "ai" && <div className="flex-1 min-h-0"><AIAssistantDrawer isOpen onClose={() => setTab("chat")} contactName={contact.contactName} threadId={via?.id || contact.activeAccountId} embedded onUseSuggestion={(t) => { setTab("chat"); setPendingText(t); }} /></div>}

      <TransferDialog open={transferOpen} onOpenChange={setTransferOpen} threadIds={threadIds} onDone={refreshThreads} />
      <Modal open={classifyOpen} onOpenChange={setClassifyOpen} title="Classify lead" description="Sets customer_type; pipelines and gates follow the type."
        footer={<><Button variant="ghost" onClick={() => setClassifyOpen(false)}>Cancel</Button><Button variant="primary" disabled={!classifyType} onClick={async () => { if (crmPrimary) await crm.classifyLead(crmPrimary.name, classifyType); setClassifyOpen(false); }}>Classify</Button></>}>
        <div className="grid gap-2 [grid-template-columns:repeat(2,minmax(0,1fr))]">
          {crmOpts.customer_types.map((t) => <button key={t} type="button" onClick={() => setClassifyType(t)} className={`h-10 rounded-md border text-sm ${classifyType === t ? "border-crayon-blue-base bg-crayon-blue-tint text-crayon-blue-text" : "border-border text-ink-2 hover:bg-surface-hover"}`}>{t}</button>)}
        </div>
      </Modal>
      <Modal open={tagsOpen} onOpenChange={setTagsOpen} title="Tags" description="Tags apply to the active reply-via thread.">
        <TagManager threadId={via?.id || contact.activeAccountId} inline onChanged={refreshThreads} />
      </Modal>
    </section>
  );
}
