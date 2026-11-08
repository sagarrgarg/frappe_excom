import { useEffect, useRef, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import type { EmailDraft } from "../components/record/Composer";

/** What the server keeps (api.email.get_email_draft / save_email_draft). */
interface ServerDraft {
  name: string; to: string; cc: string; bcc: string; subject: string; body_html: string;
  in_reply_to_message: string; author_name: string; saved_at: string; synced: boolean; sync_error: string; origin: string;
}

export type DraftState = "" | "saving" | "saved" | "local";

const SAVE_AFTER_MS = 2000;

const hasText = (html?: string) => Boolean((html || "").replace(/<[^>]+>|&nbsp;/g, "").trim());

/**
 * Drafts kept in step with the mailbox (server: channels/email/drafts.py). Opening an email
 * conversation brings back its draft — one written here or in Gmail / the mail app; typing saves
 * it a moment after you stop, here and in the mailbox's Drafts. `state` says where it is saved:
 * "saved" (both), "local" (here only: the mailbox could not be reached; `problem` says why).
 */
export function useEmailDraft(threadId: string, isEmail: boolean, draft: EmailDraft | null, setDraft: (d: EmailDraft | null) => void) {
  const [state, setState] = useState<DraftState>("");
  const [problem, setProblem] = useState("");
  const [by, setBy] = useState("");
  const { call: save } = useFrappePostCall("excom.excom.api.email.save_email_draft");
  const { call: discardCall } = useFrappePostCall("excom.excom.api.email.discard_email_draft");
  const { call: load } = useFrappePostCall("excom.excom.api.email.get_email_draft");
  const lastSaved = useRef("");
  const loadedFor = useRef("");
  const draftRef = useRef(draft);
  draftRef.current = draft;

  // Load the conversation's draft once per conversation.
  useEffect(() => {
    if (!isEmail || !threadId || loadedFor.current === threadId) return;
    loadedFor.current = threadId;
    setState(""); setProblem(""); setBy(""); lastSaved.current = "";
    let gone = false;
    load({ thread_id: threadId })
      .then((r: { message?: ServerDraft | null }) => {
        const d = r?.message;
        if (gone || !d) return;
        if (hasText(draftRef.current?.html)) return; // already typing: keep what is on screen
        const next: EmailDraft = { to: d.to, cc: d.cc, bcc: d.bcc, subject: d.subject, html: d.body_html, inReplyToGmailId: d.in_reply_to_message || "", draftName: d.name };
        lastSaved.current = snapshot(next);
        setDraft(next);
        setState(d.synced ? "saved" : "local"); setProblem(d.sync_error); setBy(d.author_name);
      })
      .catch(() => { /* no draft to bring back is not an error */ });
    return () => { gone = true; };
  }, [threadId, isEmail, setDraft, load]);

  // Save a moment after typing stops.
  useEffect(() => {
    if (!isEmail || !threadId || !draft) return;
    const snap = snapshot(draft);
    if (snap === lastSaved.current || (!hasText(draft.html) && !draft.draftName)) return;
    const t = setTimeout(async () => {
      const d = draftRef.current;
      if (!d || snapshot(d) !== snap) return;
      setState("saving");
      try {
        const r: any = await save({ thread_id: threadId, to: d.to, cc: d.cc || "", bcc: d.bcc || "", subject: d.subject, body_html: d.html || "", in_reply_to_message: d.inReplyToGmailId || "", draft_name: d.draftName || "" });
        const sd: ServerDraft | undefined = r?.message;
        lastSaved.current = snap;
        if (sd) {
          // Remember which draft this is, so the next save replaces it instead of starting another.
          if (!d.draftName && draftRef.current) setDraft({ ...draftRef.current, draftName: sd.name });
          setState(sd.synced ? "saved" : "local"); setProblem(sd.sync_error); setBy("");
        }
      } catch { setState("local"); setProblem("Not saved: the server did not answer"); }
    }, SAVE_AFTER_MS);
    return () => clearTimeout(t);
  }, [draft, threadId, isEmail, save, setDraft]);

  const discard = async () => {
    const d = draftRef.current;
    if (d?.draftName) { try { await discardCall({ draft_name: d.draftName }); } catch { /* gone already */ } }
    lastSaved.current = "";
    setState(""); setProblem(""); setBy("");
    if (d) setDraft({ ...d, subject: "", html: "", inReplyToGmailId: "", sendAt: "", draftName: "" });
  };

  /** After a send: the draft went with it. */
  const sent = () => { lastSaved.current = ""; setState(""); setProblem(""); setBy(""); };

  return { state, problem, by, discard, sent };
}

function snapshot(d: EmailDraft): string {
  return JSON.stringify([d.to, d.cc || "", d.bcc || "", d.subject, d.html || "", d.inReplyToGmailId || ""]);
}
