import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { EmailEditor } from "./EmailEditor";
import { RecipientChips } from "./RecipientChips";
import { Send, Plus, Paperclip, Image as ImageIcon, FileText, Sticker, Zap, Loader2, X, Reply, StickyNote, MessageCircle, Sparkles, Bot, ChevronDown, ChevronUp } from "lucide-react";
import { ReplyVia } from "./ReplyVia";
import { Button, Menu, menuItemClass, Kbd, Chip, Input } from "../primitives";
import { CannedResponsePopover } from "../CannedResponsePopover";
import { WhatsAppTemplatePicker } from "../WhatsAppTemplatePicker";
import { StickerPicker } from "../StickerPicker";
import { useFileUpload } from "../../hooks/useFileUpload";
import { useWindowStatus } from "../../hooks/useWindowStatus";
import { useAISuggestions } from "../../hooks/useAISuggestions";
import { MOD } from "../../lib/hotkeys";
import { cn } from "../ui/utils";
import type { Account, UnifiedContact } from "../../types";
import type { FeedMessage } from "../../hooks/useIdentityMessages";

const CHAR_LIMIT = 4096;

export interface EmailDraft { to: string; subject: string; cc: string; bcc?: string; inReplyToGmailId: string; html?: string; sendAt?: string }

interface Props {
  contact: UnifiedContact;
  via: Account | null;
  setVia: (a: Account) => void;
  replyingTo: FeedMessage | null;
  clearReply: () => void;
  emailDraft: EmailDraft | null;
  setEmailDraft: (d: EmailDraft | null) => void;
  onSent: () => void;
  onOptimistic: (o: { id: string; content: string; timestamp: Date } | null, remove?: string) => void;
  fileUpload: ReturnType<typeof useFileUpload>;
  /** Text handed over from the AI tab ("use this reply"). */
  prefill?: string | null;
  onPrefillConsumed?: () => void;
}

/**
 * Composer (UX-001 §6.2): Reply via ▾ + state, growing textarea, Message ● Note mode (note tints amber),
 * `+` menu (attach/image/template/sticker/canned), inline AI suggestions (one line, dismissible),
 * email fields expand in place when Reply via = Email. Template-required and no-access states explain
 * themselves instead of failing silently. ⌘⏎ sends; Enter sends on pointer devices.
 */
export function Composer({ contact, via, setVia, replyingTo, clearReply, emailDraft, setEmailDraft, onSent, onOptimistic, fileUpload, prefill, onPrefillConsumed }: Props) {
  const [text, setText] = useState("");
  const [note, setNote] = useState(false);
  const [canned, setCanned] = useState(false);
  const [cannedSearch, setCannedSearch] = useState("");
  const [templateOpen, setTemplateOpen] = useState(false);
  const [stickerOpen, setStickerOpen] = useState(false);
  const [stickerPos, setStickerPos] = useState({ bottom: 0, left: 0 });
  const [emailHeadOpen, setEmailHeadOpen] = useState(false);
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const stickerBtn = useRef<HTMLButtonElement>(null);
  const isWhatsApp = via?.channel === "whatsapp";
  const isMetaDm = via?.channel === "instagram" || via?.channel === "messenger";
  const isEmail = via?.channel === "email";
  const threadId = via?.id || "";

  const { window: win } = useWindowStatus(threadId, isWhatsApp || isMetaDm);
  const templateRequired = isWhatsApp && win !== null && !win.window_open && !note;
  // Instagram / Messenger have no templates: outside the window only a HUMAN_AGENT-tagged reply (if approved) can go.
  const dmWindowClosed = isMetaDm && win !== null && !win.window_open && !win.human_agent_ok && !note;
  const blocked = !via || !via.hasAccess || dmWindowClosed;

  const { call: sendMessage, loading: sending } = useFrappePostCall("excom.excom.api.chat.send_message");
  const { call: sendNote, loading: sendingNote } = useFrappePostCall("excom.excom.api.chat.send_internal_note");
  const { call: sendEmail, loading: sendingEmail } = useFrappePostCall("excom.excom.api.email.send_email");
  const { call: assignThread } = useFrappePostCall("excom.excom.api.chat.assign_thread");
  const { data: sigRaw } = useFrappeGetCall<{ message: { exists: boolean; signature_html: string } }>(isEmail ? "excom.excom.api.email.get_my_signature" : (null as unknown as string));
  const signature = sigRaw?.message?.exists ? sigRaw.message.signature_html || "" : "";
  const { suggestions } = useAISuggestions(!suggestionsDismissed && threadId ? threadId : null);
  const replies = suggestions.suggested_replies.slice(0, 3);

  useEffect(() => { setText(""); setNote(false); setCanned(false); setSuggestionsDismissed(false); }, [contact.id]);
  useEffect(() => { if (replyingTo) { setNote(false); taRef.current?.focus(); } }, [replyingTo]);
  useEffect(() => { if (prefill) { setText(prefill); setNote(false); taRef.current?.focus(); onPrefillConsumed?.(); } }, [prefill, onPrefillConsumed]);
  useEffect(() => {
    // Email: open draft fields in place when switching to an email account.
    if (isEmail && !emailDraft) { const to = contact.contactInfo.email || ""; setEmailDraft({ to, subject: "", cc: "", inReplyToGmailId: "" }); setEmailHeadOpen(!to); }
    if (!isEmail && emailDraft) setEmailDraft(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEmail, via?.id]);

  // Auto-grow
  useEffect(() => {
    const el = taRef.current; if (!el) return;
    el.style.height = "0px";
    el.style.height = Math.min(200, Math.max(36, el.scrollHeight)) + "px";
  }, [text]);

  const onChange = (v: string) => {
    if (v.length > CHAR_LIMIT) return;
    setText(v);
    if (!note && v.startsWith("/") && v.length > 1) { setCanned(true); setCannedSearch(v.slice(1)); }
    else if (!v.startsWith("/")) { setCanned(false); setCannedSearch(""); }
  };

  const send = useCallback(async () => {
    const body = text.trim();
    if (!threadId || sending || sendingNote || sendingEmail) return;

    if (note) {
      if (!body) return;
      setText("");
      try { await sendNote({ thread_id: threadId, content: body }); onSent(); toast.success("Internal note added"); }
      catch { setText(body); toast.error("Failed to add note"); }
      return;
    }
    if (blocked) { toast.error("You don't have access to send from this account"); return; }
    if (isEmail) {
      const html = (emailDraft?.html || "").trim();
      if (!emailDraft?.to.trim() || !html || html === "<br>") { toast.error("Recipient and body are required"); return; }
      const finalBody = signature ? `${html}<br><br><div class="excom-signature">${signature}</div>` : html;
      try {
        const r = await sendEmail({ thread_id: threadId, to: emailDraft.to.trim(), subject: emailDraft.subject.trim() || "(No Subject)", body_html: finalBody, cc: (emailDraft.cc || "").trim(), bcc: (emailDraft.bcc || "").trim(), in_reply_to_gmail_id: emailDraft.inReplyToGmailId, send_at: emailDraft.sendAt || "" });
        setText(""); setEmailDraft({ ...emailDraft, subject: "", inReplyToGmailId: "", html: "", sendAt: "" }); onSent(); toast.success(r?.message?.scheduled ? `Email scheduled for ${r.message.send_at.slice(0, 16)}` : "Email sent");
      } catch { toast.error("Failed to send email"); }
      return;
    }
    if (templateRequired) { setTemplateOpen(true); return; }
    if (!body) return;
    const tempId = `opt_${Date.now()}`;
    const replyTo = replyingTo?.id || "";
    setText(""); clearReply();
    onOptimistic({ id: tempId, content: body, timestamp: new Date() });
    try { await sendMessage({ thread_id: threadId, message: body, reply_to: replyTo }); onOptimistic(null, tempId); onSent(); }
    catch (e: any) { onOptimistic(null, tempId); setText(body); toast.error(e?.message || "Failed to send"); }
  }, [text, threadId, note, blocked, isEmail, emailDraft, signature, templateRequired, replyingTo, sending, sendingNote, sendingEmail, sendMessage, sendNote, sendEmail, onSent, onOptimistic, clearReply, setEmailDraft]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (canned) return;
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); send(); return; }
    if (e.key === "Enter" && !e.shiftKey && !isEmail && !window.matchMedia("(pointer: coarse)").matches) { e.preventDefault(); send(); }
    if (e.key === "Escape" && replyingTo) clearReply();
  };

  const busy = sending || sendingNote || sendingEmail;
  const placeholder = note ? "Write an internal note — only your team sees this"
    : blocked ? "No access to this account — pick another in Reply via"
    : templateRequired ? "Session closed — send an approved template to reopen"
    : dmWindowClosed ? "Reply window closed — the customer must message first (24h rule)"
    : isEmail ? "Write your email…"
    : `Message ${via?.identifier || contact.contactName}  ·  / for canned responses`;

  const plusItems = useMemo(() => [
    { id: "attach", label: "Attach file", icon: <Paperclip />, onSelect: fileUpload.openFilePicker },
    { id: "image", label: "Image", icon: <ImageIcon />, onSelect: fileUpload.openFilePicker },
    ...(isWhatsApp ? [
      { id: "template", label: "WhatsApp template", icon: <FileText />, onSelect: () => setTemplateOpen(true) },
      { id: "sticker", label: "Sticker", icon: <Sticker />, onSelect: () => { const r = stickerBtn.current?.getBoundingClientRect(); if (r) setStickerPos({ bottom: window.innerHeight - r.top + 8, left: Math.max(8, r.left) }); setStickerOpen(true); } },
    ] : []),
    { id: "canned", label: "Canned response", icon: <Zap />, onSelect: () => { setText("/"); setCanned(true); setCannedSearch(""); taRef.current?.focus(); } },
  ], [fileUpload.openFilePicker, isWhatsApp]);

  return (
    <div className={cn("shrink-0 border-t border-border safe-area-bottom", note ? "bg-crayon-amber-tint" : "bg-surface")}>
      <div className="mx-auto max-w-[900px] px-3 pt-2 pb-2">
        {/* AI monitoring notice — inline, one line */}
        {contact.aiStatus === "active" && (
          <div className="flex items-center gap-2 text-xs text-ink-2 mb-1.5 min-w-0">
            <Bot className="size-4 text-crayon-violet-base shrink-0" /><span className="truncate">AI is replying on this thread until you take over.</span>
            <Button size="sm" variant="ghost" className="ml-auto shrink-0" onClick={async () => { try { await assignThread({ thread_id: threadId }); toast.success("You took over"); onSent(); } catch { toast.error("Failed"); } }}>Take over</Button>
          </div>
        )}

        {/* Suggested replies — one line, dismissible (T2) */}
        {!note && replies.length > 0 && !suggestionsDismissed && (
          <div className="flex items-center gap-1.5 mb-1.5 min-w-0">
            <Sparkles className="size-3.5 text-crayon-violet-base shrink-0" />
            <div className="chip-row flex-1 h-6">
              {replies.map((r, i) => <Chip key={i} size="sm" accent="violet" label={r.text} onClick={() => { setText(r.text); taRef.current?.focus(); }} title={r.text} className="max-w-[260px]" />)}
            </div>
            <button type="button" aria-label="Dismiss suggestions" className="text-ink-3 hover:text-ink-1 shrink-0" onClick={() => setSuggestionsDismissed(true)}><X className="size-3.5" /></button>
          </div>
        )}

        {/* Reply bar */}
        {replyingTo && (
          <div className="flex items-center gap-2 rounded-md bg-surface-sunken border-l-[3px] border-crayon-blue-base px-2 py-1 mb-1.5 min-w-0">
            <Reply className="size-4 text-crayon-blue-base shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs text-crayon-blue-text truncate">Replying to {replyingTo.sentBy?.name || (replyingTo.sender === "user" ? "you" : contact.contactName)}</p>
              <p className="text-xs text-ink-2 truncate">{replyingTo.content}</p>
            </div>
            <button type="button" aria-label="Cancel reply" onClick={clearReply} className="text-ink-3 hover:text-ink-1 shrink-0"><X className="size-4" /></button>
          </div>
        )}

        {/* Reply via + state */}
        <div className="flex items-center gap-2 mb-1.5 min-w-0">
          {note ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-crayon-amber-text"><StickyNote className="size-4" />Internal note · not sent to the contact</span>
          ) : (
            <ReplyVia accounts={contact.allAccounts} value={via} onChange={setVia} window={win} onCall={contact.channels.includes("calls") ? () => toast.info("Calling arrives with Phase C") : undefined} />
          )}
          <div className="ml-auto flex items-center gap-1 shrink-0">
            {/* Message ● Note mode */}
            <div className="inline-flex rounded-md bg-surface-sunken p-0.5" role="radiogroup" aria-label="Compose mode">
              <button type="button" role="radio" aria-checked={!note} onClick={() => { setNote(false); setCanned(false); }} className={cn("inline-flex items-center gap-1 h-6 px-2 rounded text-xs font-medium [&_svg]:size-3.5", !note ? "bg-surface text-ink-1 shadow-ex" : "text-ink-3 hover:text-ink-1")}><MessageCircle />Message</button>
              <button type="button" role="radio" aria-checked={note} onClick={() => { setNote(true); setCanned(false); }} className={cn("inline-flex items-center gap-1 h-6 px-2 rounded text-xs font-medium [&_svg]:size-3.5", note ? "bg-surface text-crayon-amber-text shadow-ex" : "text-ink-3 hover:text-ink-1")}><StickyNote />Note</button>
            </div>
          </div>
        </div>

        {/* Email fields — grow in place */}
        {isEmail && !note && emailDraft && (
          emailHeadOpen ? (
            <div className="rounded-md border border-border-strong bg-surface mb-1.5 divide-y divide-border">
              <button type="button" onClick={() => setEmailHeadOpen(false)} className="w-full flex items-center gap-1 px-2 h-6 text-2xs text-ink-3 hover:text-ink-1"><ChevronUp className="size-3" />Collapse</button>
            <RecipientChips label="To" value={emailDraft.to} onChange={(v) => setEmailDraft({ ...emailDraft, to: v })} placeholder="recipient@example.com" />
            <RecipientChips label="Cc" value={emailDraft.cc || ""} onChange={(v) => setEmailDraft({ ...emailDraft, cc: v })} placeholder="tag colleagues or contacts…" />
            <RecipientChips label="Bcc" value={emailDraft.bcc || ""} onChange={(v) => setEmailDraft({ ...emailDraft, bcc: v })} placeholder="" />
            <div className="flex items-center gap-2 px-2 h-8 min-w-0"><span className="text-xs text-ink-3 w-12 shrink-0">Send</span>
              {emailDraft.sendAt ? <><input type="datetime-local" value={emailDraft.sendAt.replace(" ", "T").slice(0, 16)} onChange={(e) => setEmailDraft({ ...emailDraft, sendAt: e.target.value.replace("T", " ") })} className="h-7 text-xs bg-transparent outline-none text-ink-1" /><button type="button" className="text-xs text-ink-3 hover:text-ink-1" onClick={() => setEmailDraft({ ...emailDraft, sendAt: "" })}>now instead</button></>
                : <button type="button" className="text-xs text-ink-3 hover:text-ink-1" onClick={() => { const d = new Date(Date.now() + 60 * 60 * 1000); d.setSeconds(0, 0); const pad = (n: number) => String(n).padStart(2, "0"); setEmailDraft({ ...emailDraft, sendAt: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:00` }); }}>now · schedule for later…</button>}
            </div>
            <div className="flex items-center gap-2 px-2 h-8 min-w-0"><span className="text-xs text-ink-3 w-12 shrink-0">Subject</span><Input value={emailDraft.subject} onChange={(e) => setEmailDraft({ ...emailDraft, subject: e.target.value })} className="border-0 h-7 px-0 focus-visible:ring-0" placeholder="Subject" />{emailDraft.inReplyToGmailId && <Chip size="sm" accent="blue" label="Reply" onRemove={() => setEmailDraft({ ...emailDraft, inReplyToGmailId: "", subject: "" })} />}</div>
          </div>
          ) : (
            <button type="button" onClick={() => setEmailHeadOpen(true)} className="w-full flex items-center gap-2 px-2 h-8 mb-1.5 rounded-md border border-border-strong bg-surface text-xs text-left min-w-0 hover:bg-surface-hover" title="Edit recipients, subject, schedule">
              <ChevronDown className="size-3.5 text-ink-3 shrink-0" />
              <span className="text-ink-3 shrink-0">To</span><span className="text-ink-1 truncate max-w-[40%]">{emailDraft.to || "—"}</span>
              {(emailDraft.cc || emailDraft.bcc) && <span className="text-ink-3 shrink-0">+{[emailDraft.cc, emailDraft.bcc].filter(Boolean).join(",").split(",").filter((x) => x.trim()).length} cc</span>}
              <span className="text-ink-3 shrink-0">·</span><span className={cn("truncate flex-1", emailDraft.subject ? "text-ink-1" : "text-ink-3")}>{emailDraft.subject || "No subject"}</span>
              {emailDraft.inReplyToGmailId && <Chip size="sm" accent="blue" label="Reply" />}
              <span className="text-ink-3 shrink-0">{emailDraft.sendAt ? `⏱ ${emailDraft.sendAt.slice(0, 16)}` : "now"}</span>
            </button>
          )
        )}

        {/* Input row */}
        <div className="relative flex items-end gap-1.5 min-w-0">
          <CannedResponsePopover isOpen={canned} searchText={cannedSearch} channel={via?.channel} onSelect={(c) => { setText(c); setCanned(false); setCannedSearch(""); taRef.current?.focus(); }} onClose={() => { setCanned(false); setCannedSearch(""); }} />
          {!note && (
            <Menu.Root modal={false}>
              <Menu.Trigger asChild>
                <Button ref={stickerBtn} variant="ghost" size="icon" aria-label="Add" title="Attach, template, sticker, canned" disabled={fileUpload.uploading} className="shrink-0">{fileUpload.uploading ? <Loader2 className="animate-spin" /> : <Plus />}</Button>
              </Menu.Trigger>
              <Menu.Portal>
                <Menu.Content side="top" align="start" sideOffset={6} className="z-50 min-w-[200px] rounded-lg border border-border bg-surface p-1 shadow-ex">
                  {plusItems.map((it) => <Menu.Item key={it.id} className={menuItemClass} onSelect={it.onSelect}>{it.icon}{it.label}</Menu.Item>)}
                </Menu.Content>
              </Menu.Portal>
            </Menu.Root>
          )}
          <div className={cn("flex-1 min-w-0 rounded-lg border bg-surface", note ? "border-crayon-amber-base/60 focus-within:border-crayon-amber-base" : "border-border-strong focus-within:border-crayon-blue-base", (blocked && !note) && "opacity-70")}>
            {isEmail && !note && emailDraft ? (
              <EmailEditor value={emailDraft.html || ""} onChange={(h) => setEmailDraft({ ...emailDraft, html: h })} placeholder="Write your email…" />
            ) : (
            <textarea
              ref={taRef}
              value={text}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              rows={1}
              disabled={!note && blocked}
              aria-label={note ? "Internal note" : "Message"}
              className="block w-full resize-none bg-transparent px-3 py-2 text-base text-ink-1 placeholder:text-ink-3 outline-none min-h-[36px] max-h-[200px]"
            />
            )}
            {text.length > CHAR_LIMIT * 0.9 && <div className={cn("text-right text-xs px-3 pb-1 tabular-nums", text.length >= CHAR_LIMIT ? "text-crayon-rose-text" : "text-ink-3")}>{text.length}/{CHAR_LIMIT}</div>}
          </div>
          {templateRequired && !note ? (
            <Button variant="primary" onClick={() => setTemplateOpen(true)} className="shrink-0"><FileText />Template</Button>
          ) : (
            <Button variant={note ? "default" : "primary"} size="icon" onClick={send} disabled={busy || (!note && blocked) || (!text.trim() && !isEmail) || (isEmail && !note && !(emailDraft?.html || "").replace(/<[^>]+>/g, "").trim())} aria-label={note ? "Add note" : "Send"} title={`${note ? "Add note" : "Send"}  ${MOD}+⏎`} className={cn("shrink-0", note && "bg-crayon-amber-base text-white border-transparent hover:bg-crayon-amber-text")}>
              {busy ? <Loader2 className="animate-spin" /> : note ? <StickyNote /> : <Send />}
            </Button>
          )}
        </div>
        <div className="hidden laptop:flex items-center gap-1 mt-1 text-xs text-ink-3"><Kbd>⏎</Kbd> send · <Kbd>⇧⏎</Kbd> newline · <Kbd>/</Kbd> canned</div>
      </div>

      <input ref={fileUpload.inputRef} type="file" accept={fileUpload.acceptedTypes} onChange={fileUpload.handleFileChange} className="hidden" />
      {templateOpen && threadId && <WhatsAppTemplatePicker threadId={threadId} onClose={() => setTemplateOpen(false)} onSent={onSent} />}
      {stickerOpen && threadId && createPortal(
        <div style={{ position: "fixed", bottom: stickerPos.bottom, left: stickerPos.left, zIndex: 60 }}>
          <StickerPicker threadId={threadId} onClose={() => setStickerOpen(false)} onSent={onSent} />
        </div>,
        document.body
      )}
    </div>
  );
}
