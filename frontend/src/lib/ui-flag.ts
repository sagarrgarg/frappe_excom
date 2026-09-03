/** Per-user UI helpers: density, current user, roles. (The legacy/next switch was removed with the legacy tree.) */
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
