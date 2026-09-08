import * as React from "react";
import { AlertTriangle, Check, ChevronDown, Copy } from "lucide-react";
import { toast } from "sonner";
import { Modal, Button } from "./primitives";
import { errorReport, parseError, type ParsedError } from "../lib/errors";

/**
 * The error surface for the whole app: what happened, in the server's words, and a Copy button —
 * the same deal Frappe Desk offers, because "Failed to start conversation" with no cause and
 * nothing to paste wastes the agent's time and then ours.
 *
 * Call showError(err, "starting a conversation") from any catch block.
 */

type Listener = (p: ParsedError) => void;
let listener: Listener | null = null;

export function showError(err: unknown, context = ""): ParsedError {
  const parsed = parseError(err, context);
  if (listener) listener(parsed);
  else toast.error(parsed.message.split("\n")[0] || parsed.title);
  return parsed;
}

/**
 * A failed action, as a toast that actually says what the server said, with the full report one
 * click away. `fallback` is the old hardcoded sentence — kept as the heading so the toast still
 * reads like a sentence when the server was terse.
 */
export function toastError(err: unknown, fallback = "Something went wrong"): ParsedError {
  const parsed = parseError(err, fallback);
  const detail = parsed.message.split("\n").find((l) => l.trim()) || "";
  toast.error(fallback, {
    description: detail && detail !== fallback ? detail : undefined,
    action: { label: "Details", onClick: () => listener?.(parsed) },
    duration: 8000,
  });
  return parsed;
}

export function ErrorDialogHost() {
  const [error, setError] = React.useState<ParsedError | null>(null);
  const [showTrace, setShowTrace] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    listener = (p) => {
      setShowTrace(false);
      setCopied(false);
      setError(p);
    };
    return () => {
      listener = null;
    };
  }, []);

  const copy = async () => {
    if (!error) return;
    const text = errorReport(error);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard is blocked outside a secure context; a selectable textarea still gets it copied.
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!error) return null;

  return (
    <Modal
      open
      onOpenChange={(v) => !v && setError(null)}
      title={
        <span className="flex items-center gap-2 text-crayon-rose-text">
          <AlertTriangle className="size-4 shrink-0" />
          {error.title}
        </span>
      }
      width="max-w-lg"
      footer={
        <>
          <Button variant="subtle" onClick={copy}>
            {copied ? <Check /> : <Copy />}
            {copied ? "Copied" : "Copy error"}
          </Button>
          <Button variant="primary" onClick={() => setError(null)}>
            Close
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error.context && (
          <p className="text-xs text-ink-3">While {error.context}</p>
        )}
        <p className="text-sm text-ink-1 whitespace-pre-wrap break-words">{error.message}</p>

        {(error.excType || error.httpStatus) && (
          <p className="text-xs text-ink-3 font-mono">
            {[error.excType, error.httpStatus ? `HTTP ${error.httpStatus}` : ""].filter(Boolean).join(" · ")}
          </p>
        )}

        {error.traceback && (
          <div>
            <button
              type="button"
              className="flex items-center gap-1 text-xs text-ink-2 hover:text-ink-1"
              onClick={() => setShowTrace((v) => !v)}
            >
              <ChevronDown className={`size-3.5 transition-transform ${showTrace ? "" : "-rotate-90"}`} />
              Technical details
            </button>
            {showTrace && (
              <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-surface-sunken p-2 text-xs leading-relaxed text-ink-2 whitespace-pre-wrap break-words">
                {error.traceback}
              </pre>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
