import { useState, useRef, useEffect, useCallback } from "react";
import {
  Mail,
  ChevronDown,
  ChevronUp,
  Paperclip,
  Loader2,
  AlertTriangle,
  Reply,
  Download,
  FileText,
  FileImage,
  FileSpreadsheet,
  FileArchive,
  File,
  FileVideo,
  FileAudio,
  ExternalLink,
  ReplyAll,
  Forward,
  Star,
} from "lucide-react";
import { Badge } from "./ui/badge";
import { formatServerShortDateTime } from "../utils/datetime";

interface EmailAttachment {
  filename: string;
  mimeType: string;
  size: number;
  attachmentId: string;
}

interface EmailBodyData {
  body_html: string;
  body_text: string;
  subject: string;
  from_email: string;
  from_name: string;
  to: string;
  cc: string;
  date: string;
  attachments: EmailAttachment[];
  deleted: boolean;
  error?: string;
}

interface EmailMessageCardProps {
  messageId: string;
  direction: "Inbound" | "Outbound";
  snippet: string;
  timestamp: Date;
  contentJson: string;
  sentBy?: { name: string; avatar: string };
  bodyData?: EmailBodyData;
  bodyLoading?: boolean;
  onExpandEmail: (messageId: string) => void;
  onReplyEmail?: (messageId: string, subject: string, to: string) => void;
  onRetryFetch?: (messageId: string) => void;
  /** Sending/receiving account shown in the footer (merged feed). */
  accountName?: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileIcon(mimeType: string, filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || "";

  if (mimeType.startsWith("image/"))
    return { icon: FileImage, color: "text-crayon-green-text", bg: "bg-crayon-green-tint" };
  if (mimeType.startsWith("video/"))
    return { icon: FileVideo, color: "text-crayon-violet-text", bg: "bg-crayon-violet-tint" };
  if (mimeType.startsWith("audio/"))
    return { icon: FileAudio, color: "text-crayon-plum-text", bg: "bg-crayon-plum-tint" };
  if (mimeType === "application/pdf" || ext === "pdf")
    return { icon: FileText, color: "text-crayon-rose-text", bg: "bg-crayon-rose-tint" };
  if (["doc", "docx", "odt", "rtf", "txt"].includes(ext))
    return { icon: FileText, color: "text-crayon-blue-text", bg: "bg-crayon-blue-tint" };
  if (["xls", "xlsx", "csv", "ods"].includes(ext) || mimeType.includes("spreadsheet"))
    return { icon: FileSpreadsheet, color: "text-crayon-green-text", bg: "bg-crayon-green-tint" };
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext) || mimeType.includes("zip") || mimeType.includes("compressed"))
    return { icon: FileArchive, color: "text-crayon-amber-text", bg: "bg-crayon-amber-tint" };

  return { icon: File, color: "text-ink-3", bg: "bg-surface-active" };
}

export function EmailMessageCard({
  messageId,
  direction,
  snippet,
  timestamp,
  contentJson,
  sentBy,
  bodyData,
  bodyLoading,
  onExpandEmail,
  onReplyEmail,
  onRetryFetch,
  accountName,
}: EmailMessageCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [showHeaders, setShowHeaders] = useState(false);
  const isOutbound = direction === "Outbound";

  let emailMeta = {
    subject: "(No Subject)",
    from_email: "",
    from_name: "",
    to: "",
    cc: "",
    gmail_message_id: "",
    label_ids: [] as string[],
    message_id_header: "",
    in_reply_to: "",
  };
  try {
    const parsed = JSON.parse(contentJson || "{}");
    emailMeta = { ...emailMeta, ...parsed };
    if (typeof emailMeta.label_ids === "string") {
      try { emailMeta.label_ids = JSON.parse(emailMeta.label_ids); } catch { emailMeta.label_ids = []; }
    }
  } catch {
    // use defaults
  }

  const isStarredByGmail = emailMeta.label_ids?.includes("STARRED");
  const isImportant = emailMeta.label_ids?.includes("IMPORTANT");
  const isDraft = emailMeta.label_ids?.includes("DRAFT");

  // Initialise star state from Gmail label on first render
  const [starred, setStarred] = useState(() => isStarredByGmail);

  const handleToggle = () => {
    if (!expanded && !bodyData) {
      onExpandEmail(messageId);
    }
    setExpanded(!expanded);
  };

  const replySubject = emailMeta.subject.startsWith("Re:")
    ? emailMeta.subject
    : `Re: ${emailMeta.subject}`;
  const replyTo = isOutbound ? emailMeta.to : emailMeta.from_email;

  const hasAttachments = bodyData && bodyData.attachments.length > 0;

  return (
    <div className={`flex ${isOutbound ? "justify-end" : "justify-start"}`}>
      <div className="max-w-[85%] w-full group/email">
        <div
          className={`rounded-xl border overflow-hidden transition-shadow ${
            isOutbound
              ? "bg-crayon-blue-tint border-crayon-blue-base/40 "
              : "bg-surface-sunken border-border-strong "
          } ${expanded ? "shadow-ex" : "shadow-ex hover:shadow-ex"}`}
        >
          {/* Compact header — always visible */}
          <button
            onClick={handleToggle}
            className="w-full text-left px-3 py-2 hover:bg-surface transition-colors"
          >
            <div className="flex items-start gap-3">
              {/* Avatar */}
              <div className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${
                isOutbound
                  ? "bg-crayon-blue-tint text-crayon-blue-text"
                  : "bg-surface-active text-ink-2"
              }`}>
                {(emailMeta.from_name || emailMeta.from_email || "?")
                  .split(" ")
                  .map((w) => w[0])
                  .join("")
                  .slice(0, 2)
                  .toUpperCase()}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-medium text-ink-1 truncate">
                    {isOutbound
                      ? `To: ${emailMeta.to}`
                      : emailMeta.from_name || emailMeta.from_email}
                  </span>
                  <span className="text-xs text-ink-3 shrink-0 tabular-nums">
                    {formatServerShortDateTime(timestamp)}
                  </span>
                  <div className="flex items-center gap-1 ml-auto shrink-0">
                    {hasAttachments && !expanded && (
                      <Paperclip className="w-3 h-3 text-ink-3" />
                    )}
                    {expanded ? (
                      <ChevronUp className="w-3.5 h-3.5 text-ink-3" />
                    ) : (
                      <ChevronDown className="w-3.5 h-3.5 text-ink-3" />
                    )}
                  </div>
                </div>
                <p className="text-sm text-ink-1 truncate font-medium">
                  {emailMeta.subject}
                </p>
                {!expanded && (
                  <p className="text-xs text-ink-3 mt-1 line-clamp-1">
                    {snippet}
                  </p>
                )}
                {/* Gmail label badges */}
                {!expanded && (isDraft || isImportant || starred) && (
                  <div className="flex items-center gap-1 mt-1">
                    {isDraft && (
                      <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-crayon-amber-tint text-crayon-amber-text border border-crayon-amber-base/40">
                        Draft
                      </span>
                    )}
                    {isImportant && (
                      <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-crayon-amber-tint text-crayon-amber-text border border-crayon-amber-base/40">
                        Important
                      </span>
                    )}
                    {starred && (
                      <Star className="w-3 h-3 fill-crayon-amber-base text-crayon-amber-text" />
                    )}
                  </div>
                )}
              </div>
            </div>
          </button>

          {/* Expanded body */}
          {expanded && (
            <div className="border-t border-border-strong">
              {bodyLoading ? (
                <div className="flex items-center justify-center py-10">
                  <Loader2 className="w-5 h-5 text-crayon-blue-text animate-spin mr-2" />
                  <span className="text-sm text-ink-3">
                    Fetching from Gmail...
                  </span>
                </div>
              ) : bodyData?.deleted ? (
                <div className="flex items-start gap-3 p-3">
                  <AlertTriangle className="w-4 h-4 text-crayon-amber-text shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-crayon-amber-text">
                      {(bodyData as any).auth_error ? "Gmail authorization required" : "Email no longer available"}
                    </p>
                    <p className="text-xs text-ink-3 mt-0.5">
                      {bodyData.error || "This email has been deleted from Gmail."}
                    </p>
                    {(bodyData as any).auth_error && (
                      <a
                        href="/app/excom-channel-account"
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 mt-2 text-xs text-crayon-blue-text hover:text-crayon-blue-text underline"
                      >
                        Re-authorize email account →
                      </a>
                    )}
                    <button
                      onClick={() => {
                        // Clear cached error so fetch is retried
                        if (typeof onRetryFetch === "function") onRetryFetch(messageId);
                      }}
                      className="block mt-1.5 text-xs text-ink-3 hover:text-ink-2 underline"
                    >
                      Retry
                    </button>
                  </div>
                </div>
              ) : bodyData ? (
                <div>
                  {/* Email metadata — From, To, CC, Date */}
                  <div className="px-3 py-2.5 bg-surface-sunken text-xs space-y-1 border-b border-border-strong">
                    <div className="flex items-start gap-2">
                      <span className="text-ink-3 w-10 shrink-0 text-right">From</span>
                      <span className="text-ink-2">
                        {bodyData.from_name ? (
                          <>
                            <span className="font-medium">{bodyData.from_name}</span>
                            <span className="text-ink-3 ml-1">&lt;{bodyData.from_email}&gt;</span>
                          </>
                        ) : bodyData.from_email}
                      </span>
                    </div>
                    <div className="flex items-start gap-2">
                      <span className="text-ink-3 w-10 shrink-0 text-right">To</span>
                      <span className="text-ink-2">{bodyData.to}</span>
                    </div>
                    {bodyData.cc && (
                      <div className="flex items-start gap-2">
                        <span className="text-ink-3 w-10 shrink-0 text-right">Cc</span>
                        <span className="text-ink-2">{bodyData.cc}</span>
                      </div>
                    )}
                    {bodyData.date && (
                      <div className="flex items-start gap-2">
                        <span className="text-ink-3 w-10 shrink-0 text-right">Date</span>
                        <span className="text-ink-3">{bodyData.date}</span>
                      </div>
                    )}
                  </div>

                  {/* Action toolbar */}
                  <div className="flex items-center gap-1 px-3 py-2 border-b border-border-strong">
                    {onReplyEmail && (
                      <>
                        <button
                          onClick={() => onReplyEmail(emailMeta.gmail_message_id, replySubject, replyTo)}
                          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs text-ink-2 hover:bg-surface-active hover:text-ink-1 transition-colors"
                        >
                          <Reply className="w-3.5 h-3.5" />
                          Reply
                        </button>
                        <button
                          onClick={() => {
                            const allRecipients = [emailMeta.to, emailMeta.from_email, bodyData.cc].filter(Boolean).join(", ");
                            onReplyEmail(emailMeta.gmail_message_id, replySubject, allRecipients);
                          }}
                          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs text-ink-2 hover:bg-surface-active hover:text-ink-1 transition-colors"
                        >
                          <ReplyAll className="w-3.5 h-3.5" />
                          Reply All
                        </button>
                        <button
                          onClick={() => {
                            const fwdSubject = emailMeta.subject.startsWith("Fwd:")
                              ? emailMeta.subject
                              : `Fwd: ${emailMeta.subject}`;
                            onReplyEmail(emailMeta.gmail_message_id, fwdSubject, "");
                          }}
                          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs text-ink-2 hover:bg-surface-active hover:text-ink-1 transition-colors"
                        >
                          <Forward className="w-3.5 h-3.5" />
                          Forward
                        </button>
                      </>
                    )}
                    <div className="flex-1" />
                    <button
                      onClick={() => setStarred(!starred)}
                      className={`p-1.5 rounded-md transition-colors ${
                        starred
                          ? "text-crayon-amber-text bg-crayon-amber-tint"
                          : "text-ink-3 hover:text-crayon-amber-text hover:bg-surface-active"
                      }`}
                      title={starred ? "Unstar" : "Star"}
                    >
                      <Star className={`w-3.5 h-3.5 ${starred ? "fill-crayon-amber-base" : ""}`} />
                    </button>
                    <button
                      onClick={() => {
                        const w = window.open("", "_blank");
                        if (w) {
                          w.document.write(bodyData.body_html || `<pre>${bodyData.body_text}</pre>`);
                          w.document.close();
                          w.print();
                        }
                      }}
                      className="p-1.5 rounded-md text-ink-3 hover:text-ink-2 hover:bg-surface-active transition-colors"
                      title="Open in new tab / Print"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => setShowHeaders(!showHeaders)}
                      className={`px-2 py-1 rounded-md text-xs transition-colors ${
                        showHeaders
                          ? "bg-surface-active text-ink-1"
                          : "text-ink-3 hover:text-ink-2 hover:bg-surface-active"
                      }`}
                      title="Show original headers"
                    >
                      Headers
                    </button>
                  </div>

                  {/* Original headers panel */}
                  {showHeaders && (
                    <div className="mx-4 mb-2 p-3 rounded-lg bg-surface border border-border-strong text-xs font-mono text-ink-3 space-y-0.5 max-h-40 overflow-y-auto">
                      {bodyData.from_email && <div><span className="text-ink-3">From:</span> {bodyData.from_name ? `${bodyData.from_name} <${bodyData.from_email}>` : bodyData.from_email}</div>}
                      {bodyData.to && <div><span className="text-ink-3">To:</span> {bodyData.to}</div>}
                      {bodyData.cc && <div><span className="text-ink-3">Cc:</span> {bodyData.cc}</div>}
                      {bodyData.date && <div><span className="text-ink-3">Date:</span> {bodyData.date}</div>}
                      {emailMeta.message_id_header && <div><span className="text-ink-3">Message-ID:</span> {emailMeta.message_id_header}</div>}
                      {emailMeta.in_reply_to && <div><span className="text-ink-3">In-Reply-To:</span> {emailMeta.in_reply_to}</div>}
                      {emailMeta.label_ids?.length > 0 && <div><span className="text-ink-3">Labels:</span> {emailMeta.label_ids.join(", ")}</div>}
                    </div>
                  )}

                  {/* Email body */}
                  <div className="p-3">
                    {bodyData.body_html ? (
                      <EmailBodyFrame html={bodyData.body_html} />
                    ) : (
                      <pre className="text-sm text-ink-1 whitespace-pre-wrap font-sans leading-relaxed">
                        {bodyData.body_text || "(Empty email)"}
                      </pre>
                    )}
                  </div>

                  {/* Attachments */}
                  {bodyData.attachments.length > 0 && (
                    <div className="px-3 pb-4 border-t border-border-strong pt-3">
                      <div className="flex items-center gap-2 mb-3">
                        <Paperclip className="w-3.5 h-3.5 text-ink-3" />
                        <span className="text-xs font-medium text-ink-2">
                          {bodyData.attachments.length} Attachment{bodyData.attachments.length > 1 ? "s" : ""}
                        </span>
                        <span className="text-xs text-ink-3">
                          ({formatBytes(bodyData.attachments.reduce((s, a) => s + a.size, 0))} total)
                        </span>
                      </div>
                      <div className="grid gap-2">
                        {bodyData.attachments.map((att, i) => (
                          <AttachmentCard
                            key={i}
                            attachment={att}
                            messageId={messageId}
                          />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex items-center justify-center py-10">
                  <span className="text-sm text-ink-3">
                    Click to load email content
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className={`flex items-center gap-1.5 mt-1 text-xs ${
            isOutbound ? "justify-end" : "justify-start"
          }`}
        >
          <Badge className="text-xs px-1.5 h-4 bg-surface-sunken text-ink-3 border-border-strong">
            <Mail className="w-2.5 h-2.5 mr-0.5" />
            Email
          </Badge>
          {accountName && (
            <span className="text-xs text-ink-3 truncate max-w-[180px]">{accountName}</span>
          )}
          {sentBy && (
            <span className="text-xs text-ink-3 truncate max-w-[140px]">{sentBy.name}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function AttachmentCard({
  attachment,
  messageId,
}: {
  attachment: EmailAttachment;
  messageId: string;
}) {
  const [downloading, setDownloading] = useState(false);
  const { icon: FileIcon, color, bg } = getFileIcon(attachment.mimeType, attachment.filename);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const params = new URLSearchParams({
        message_name: messageId,
        attachment_id: attachment.attachmentId,
        filename: attachment.filename,
      });
      const url = `/api/method/excom.excom.api.email.get_email_attachment?${params}`;

      const resp = await fetch(url);
      if (!resp.ok) throw new Error("Download failed");

      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = attachment.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(blobUrl);
    } catch {
      const params = new URLSearchParams({
        message_name: messageId,
        attachment_id: attachment.attachmentId,
        filename: attachment.filename,
      });
      window.open(
        `/api/method/excom.excom.api.email.get_email_attachment?${params}`,
        "_blank",
      );
    } finally {
      setDownloading(false);
    }
  };

  const ext = attachment.filename.split(".").pop()?.toUpperCase() || "FILE";

  return (
    <button
      onClick={handleDownload}
      disabled={downloading}
      className="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-border-strong bg-surface-sunken hover:bg-surface-active hover:border-border-strong transition-all group disabled:opacity-60 text-left w-full"
    >
      <div className={`w-10 h-10 rounded-lg ${bg} flex items-center justify-center shrink-0`}>
        <FileIcon className={`w-5 h-5 ${color}`} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-ink-1 truncate font-medium group-hover:text-ink-1 transition-colors">
          {attachment.filename}
        </p>
        <p className="text-xs text-ink-3 mt-0.5">
          {ext} &middot; {formatBytes(attachment.size)}
        </p>
      </div>
      <div className="shrink-0">
        {downloading ? (
          <Loader2 className="w-4 h-4 text-crayon-blue-text animate-spin" />
        ) : (
          <div className="w-8 h-8 rounded-full bg-surface-active group-hover:bg-crayon-blue-tint flex items-center justify-center transition-colors">
            <Download className="w-4 h-4 text-ink-3 group-hover:text-crayon-blue-text transition-colors" />
          </div>
        )}
      </div>
    </button>
  );
}

/**
 * Renders HTML email content inside an iframe with a light background
 * so that inline styles from Gmail (black text, white bg) remain readable
 * against the app's dark theme.
 */
function EmailBodyFrame({ html }: { html: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(200);

  const resizeFrame = useCallback(() => {
    const doc = iframeRef.current?.contentDocument;
    if (!doc?.body) return;
    const h = doc.body.scrollHeight;
    if (h > 0) setHeight(Math.min(h + 24, 2000));
  }, []);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;

    const handleLoad = () => {
      resizeFrame();
      const observer = new ResizeObserver(resizeFrame);
      if (iframe.contentDocument?.body) {
        observer.observe(iframe.contentDocument.body);
      }
      return () => observer.disconnect();
    };

    iframe.addEventListener("load", handleLoad);
    return () => iframe.removeEventListener("load", handleLoad);
  }, [resizeFrame]);

  // Email bodies render in a sandboxed iframe; colours come from the token variables so nothing is hardcoded.
  const tok = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const srcdoc = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html, body { margin: 0; padding: 12px; background: ${tok("--ex-surface")}; color: ${tok("--ex-ink-1")};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px; line-height: 1.6; word-break: break-word; }
  img { max-width: 100%; height: auto; border-radius: 4px; }
  a { color: ${tok("--ex-blue-text")}; }
  table { border-collapse: collapse; max-width: 100%; }
  td, th { padding: 4px 8px; }
  pre, code { white-space: pre-wrap; font-size: 13px; }
  blockquote { margin: 8px 0; padding-left: 12px; border-left: 3px solid ${tok("--ex-border-strong")}; color: ${tok("--ex-ink-2")}; }
</style></head><body>${html}</body></html>`;

  return (
    <iframe
      ref={iframeRef}
      srcDoc={srcdoc}
      sandbox="allow-same-origin"
      className="w-full border-0 rounded-lg bg-surface"
      style={{ height: `${height}px`, minHeight: "60px" }}
      title="Email content"
    />
  );
}
