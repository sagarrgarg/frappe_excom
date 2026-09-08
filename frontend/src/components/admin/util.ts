import { errorSummary } from "../../lib/errors";

/**
 * The one-line form of a server error, for a toast. Everything it knows about reading a Frappe
 * error — _server_messages, _error_message, exception, bare 403s — lives in lib/errors.ts, so the
 * toast and the error dialog can never disagree about what went wrong.
 */
export function serverMessage(e: unknown): string {
  return errorSummary(e, "Something went wrong");
}
