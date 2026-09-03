import { useState } from "react";
import { Send, X, Loader2 } from "lucide-react";
import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";

interface EmailComposeProps {
  threadId: string;
  defaultTo?: string;
  defaultSubject?: string;
  inReplyToGmailId?: string;
  onClose: () => void;
  onSent: () => void;
}

export function EmailCompose({
  threadId,
  defaultTo = "",
  defaultSubject = "",
  inReplyToGmailId = "",
  onClose,
  onSent,
}: EmailComposeProps) {
  const [to, setTo] = useState(defaultTo);
  const [subject, setSubject] = useState(defaultSubject);
  const [cc, setCc] = useState("");
  const [bodyHtml, setBodyHtml] = useState("");
  const [showCc, setShowCc] = useState(false);

  const { data: sigRaw } = useFrappeGetCall<{
    message: { exists: boolean; signature_html: string; position: string };
  }>("excom.excom.api.email.get_my_signature");

  const signature = sigRaw?.message?.exists ? (sigRaw.message.signature_html || "") : "";

  const { call, loading } = useFrappePostCall("excom.excom.api.email.send_email");

  const handleSend = async () => {
    if (!to.trim() || !bodyHtml.trim()) {
      toast.error("Recipient and body are required");
      return;
    }

    const finalBody = signature
      ? `${bodyHtml}<br><br><div class="excom-signature">${signature}</div>`
      : bodyHtml;

    try {
      await call({
        thread_id: threadId,
        to: to.trim(),
        subject: subject.trim() || "(No Subject)",
        body_html: finalBody,
        cc: cc.trim(),
        in_reply_to_gmail_id: inReplyToGmailId,
      });
      toast.success("Email sent");
      onSent();
      onClose();
    } catch {
      toast.error("Failed to send email");
    }
  };

  return (
    <div className="border-t border-border-strong bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="text-xs font-medium text-ink-2">
          {inReplyToGmailId ? "Reply" : "New Email"}
        </span>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-surface-sunken text-ink-3 hover:text-ink-1 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Fields */}
      <div className="px-3 py-2 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-3 w-12 shrink-0">To:</span>
          <input
            type="text"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="recipient@example.com"
            className="flex-1 bg-transparent text-sm text-ink-1 outline-none placeholder:text-ink-3"
          />
          {!showCc && (
            <button
              onClick={() => setShowCc(true)}
              className="text-xs text-ink-3 hover:text-ink-2"
            >
              Cc
            </button>
          )}
        </div>

        {showCc && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-3 w-12 shrink-0">Cc:</span>
            <input
              type="text"
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              placeholder="cc@example.com"
              className="flex-1 bg-transparent text-sm text-ink-1 outline-none placeholder:text-ink-3"
            />
          </div>
        )}

        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-3 w-12 shrink-0">Subject:</span>
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Subject"
            className="flex-1 bg-transparent text-sm text-ink-1 outline-none placeholder:text-ink-3"
          />
        </div>
      </div>

      {/* Body */}
      <div className="px-3 pb-2">
        <textarea
          value={bodyHtml}
          onChange={(e) => setBodyHtml(e.target.value)}
          placeholder="Write your message..."
          rows={6}
          className="w-full bg-surface-sunken rounded-lg p-3 text-sm text-ink-1 outline-none resize-none placeholder:text-ink-3 border border-border-strong focus:border-crayon-blue-base/40"
        />
        {signature && (
          <div className="mt-2 pt-2 border-t border-border-strong">
            <p className="text-xs text-ink-3 mb-1">Signature preview</p>
            <div
              className="text-xs text-ink-3"
              dangerouslySetInnerHTML={{ __html: signature }}
            />
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 px-3 pb-3">
        <button
          onClick={onClose}
          className="px-3 py-1.5 text-xs text-ink-3 hover:text-ink-1 transition-colors"
        >
          Discard
        </button>
        <button
          onClick={() => void handleSend()}
          disabled={loading || !to.trim() || !bodyHtml.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-crayon-blue-base hover:bg-crayon-blue-text text-white disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium rounded-lg transition-colors"
        >
          {loading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Send className="w-3.5 h-3.5" />
          )}
          Send
        </button>
      </div>
    </div>
  );
}
