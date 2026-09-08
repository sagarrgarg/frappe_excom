/**
 * One reading of a Frappe error, for the whole app.
 *
 * Every call site used to invent its own: a try/catch that parsed _server_messages and, when that
 * was absent, fell back to a sentence written at the call site — "Failed to start conversation".
 * A permission failure raised by the framework carries no _server_messages at all (it arrives in
 * _error_message), so the person saw the fallback and nothing else: no cause, no missing role, and
 * nothing to paste to whoever could fix it.
 *
 * frappe-js-sdk throws the response body spread into an object, so everything the server sent is
 * here: message, exception, exc_type, exc (traceback, only for users allowed to see it),
 * _server_messages, _error_message.
 */

export interface FrappeErrorShape {
  httpStatus?: number;
  httpStatusText?: string;
  message?: string;
  exception?: string;
  exc_type?: string;
  exc?: string;
  _error_message?: string;
  _server_messages?: string;
}

export interface ParsedError {
  /** Dialog/toast heading: "Permission denied", "Server error", … */
  title: string;
  /** What went wrong, in the server's own words, plain text with newlines. */
  message: string;
  /** Frappe's exception class, e.g. PermissionError, ValidationError. */
  excType: string;
  httpStatus?: number;
  /** Python traceback, when the server is willing to hand it over. */
  traceback: string;
  /** Whatever we were doing: the whitelisted method, or a described action. */
  context: string;
  raw: unknown;
}

const HTTP_TITLES: Record<number, string> = {
  400: "Bad request",
  401: "Session expired",
  403: "Permission denied",
  404: "Not found",
  409: "Conflict",
  413: "Too large",
  417: "Request failed",
  429: "Too many requests",
  500: "Server error",
  502: "Server unavailable",
  503: "Server unavailable",
  504: "Server timed out",
};

const EXC_TITLES: Record<string, string> = {
  PermissionError: "Permission denied",
  ValidationError: "Cannot do that",
  MandatoryError: "Something is missing",
  DuplicateEntryError: "Already exists",
  LinkValidationError: "Linked record missing",
  DoesNotExistError: "Not found",
  TimestampMismatchError: "Someone else changed this",
  AuthenticationError: "Session expired",
};

/** Frappe messages are HTML. Keep the line breaks, drop the markup, decode the few entities used. */
export function htmlToText(html: string): string {
  return html
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li)>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** The messages frappe.throw / frappe.msgprint put in _server_messages, best first. */
function serverMessages(err: FrappeErrorShape): string[] {
  const raw = err?._server_messages;
  if (!raw) return [];
  try {
    const outer = JSON.parse(raw) as unknown[];
    return outer
      .map((entry) => {
        if (typeof entry !== "string") return "";
        try {
          const inner = JSON.parse(entry) as { message?: string; title?: string };
          return inner?.message || "";
        } catch {
          return entry;
        }
      })
      .map(htmlToText)
      .filter(Boolean);
  } catch {
    return [];
  }
}

/** The title frappe.throw carried, if it set one ("Permission denied"). */
function serverTitle(err: FrappeErrorShape): string {
  const raw = err?._server_messages;
  if (!raw) return "";
  try {
    const outer = JSON.parse(raw) as unknown[];
    for (const entry of outer) {
      if (typeof entry !== "string") continue;
      const inner = JSON.parse(entry) as { title?: string };
      if (inner?.title && inner.title !== "Message") return htmlToText(inner.title);
    }
  } catch {
    /* no title */
  }
  return "";
}

export function parseError(err: unknown, context = ""): ParsedError {
  const e = (err || {}) as FrappeErrorShape;
  const excType = e.exc_type || (e.exception ? e.exception.split(":")[0].split(".").pop() || "" : "");
  const messages = serverMessages(e);

  // In order of how much the server actually told us.
  let message =
    messages.join("\n\n") ||
    (e._error_message ? htmlToText(e._error_message) : "") ||
    (e.exception ? e.exception.split(":").slice(1).join(":").trim() : "") ||
    (typeof e.message === "string" && e.message !== "There was an error." ? e.message : "") ||
    (err instanceof Error ? err.message : "");

  if (!message && e.httpStatus === 403) {
    // A bare 403 with nothing attached still has one useful thing to say.
    message = "The server refused this action for your account, without saying which permission is missing.";
  }
  if (!message) message = "The server did not say what went wrong.";

  let traceback = "";
  if (e.exc) {
    try {
      const parsed = JSON.parse(e.exc);
      traceback = Array.isArray(parsed) ? parsed.join("\n") : String(parsed);
    } catch {
      traceback = e.exc;
    }
  }
  if (!traceback && e.exception && e.exception.includes("Traceback")) traceback = e.exception;

  const title =
    serverTitle(e) ||
    EXC_TITLES[excType] ||
    (e.httpStatus ? HTTP_TITLES[e.httpStatus] : "") ||
    "Something went wrong";

  return { title, message, excType, httpStatus: e.httpStatus, traceback, context, raw: err };
}

/** Short line for a toast. The first sentence is the one that fits. */
export function errorSummary(err: unknown, fallback = ""): string {
  const p = parseError(err);
  const first = p.message.split("\n").find((l) => l.trim()) || fallback || p.title;
  return first;
}

/** What the Copy button puts on the clipboard: everything an engineer would ask for. */
export function errorReport(p: ParsedError): string {
  const lines = [
    `Excom error: ${p.title}`,
    p.context ? `Action: ${p.context}` : "",
    p.excType ? `Exception: ${p.excType}` : "",
    p.httpStatus ? `HTTP: ${p.httpStatus}` : "",
    `User: ${(window as unknown as { frappe?: { boot?: { user?: { name?: string } } } }).frappe?.boot?.user?.name || "unknown"}`,
    `Page: ${window.location.href}`,
    `Time: ${new Date().toISOString()}`,
    "",
    p.message,
  ];
  if (p.traceback) lines.push("", "Traceback:", p.traceback);
  return lines.filter((l) => l !== "").join("\n");
}
