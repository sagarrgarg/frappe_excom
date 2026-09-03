/**
 * UI tree resolution (UX-001 §10.2):
 *   ?ui=next|legacy → localStorage.excom_ui → per-user flag (boot.sysdefaults.excom_ui) → default next.
 * Legacy stays reachable with ?ui=legacy until P2 deletes it.
 */
export type UiMode = "next" | "legacy";

const LS_KEY = "excom_ui";

export function resolveUiMode(): UiMode {
  try {
    const q = new URLSearchParams(window.location.search).get("ui");
    if (q === "next" || q === "legacy") {
      localStorage.setItem(LS_KEY, q);
      return q;
    }
    const ls = localStorage.getItem(LS_KEY);
    if (ls === "next" || ls === "legacy") return ls;
  } catch {
    /* storage blocked */
  }
  const boot = (window as any).frappe?.boot;
  const fromUser = boot?.excom_ui || boot?.sysdefaults?.excom_ui;
  if (fromUser === "next" || fromUser === "legacy") return fromUser;
  return "next";
}

export function setLocalUiMode(mode: UiMode | null) {
  try {
    if (mode) localStorage.setItem(LS_KEY, mode);
    else localStorage.removeItem(LS_KEY);
  } catch {
    /* ignore */
  }
}

export function switchUi(mode: UiMode) {
  setLocalUiMode(mode);
  const url = new URL(window.location.href);
  url.searchParams.delete("ui");
  // Legacy has no routes: land on the app root.
  if (mode === "legacy") url.pathname = appBasename() || "/";
  window.location.assign(url.toString());
}

/** `/excom` in production (Frappe www page), `` in the Vite dev server. */
export function appBasename(): string {
  return window.location.pathname.startsWith("/excom") ? "/excom" : "";
}

/* ─── density ─── */
export type Density = "comfortable" | "compact";
const DENSITY_KEY = "excom_density";

export function getDensity(): Density {
  try {
    return localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

export function applyDensity(d: Density) {
  try { localStorage.setItem(DENSITY_KEY, d); } catch { /* ignore */ }
  document.documentElement.setAttribute("data-density", d);
}

/* ─── session helpers ─── */
export function currentUser(): string {
  return (window as any).frappe?.boot?.user?.name || (window as any).frappe?.session?.user || "";
}
export function currentUserFullName(): string {
  const b = (window as any).frappe?.boot;
  return b?.user?.full_name || b?.user?.name || "";
}
export function currentUserImage(): string {
  return (window as any).frappe?.boot?.user?.user_image || "";
}
export function hasRole(role: string): boolean {
  return Boolean((window as any).frappe?.boot?.user?.roles?.includes(role));
}
export function isManager(): boolean {
  return hasRole("System Manager") || hasRole("Excom Manager");
}
