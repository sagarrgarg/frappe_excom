export function serverMessage(e: unknown): string {
  const raw = (e as { _server_messages?: string })?._server_messages;
  try { if (raw) { const m = JSON.parse(raw)[0]; return JSON.parse(m).message?.replace(/<[^>]+>/g, "") || String(m); } } catch { /* fall through */ }
  const ex = (e as { exception?: string })?.exception; if (ex) return ex.split(":").slice(-1)[0].trim();
  return (e as { message?: string })?.message || "Something went wrong";
}
