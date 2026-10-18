import { Outlet } from "react-router-dom";
import { ExternalLink, LogOut, Rows3 } from "lucide-react";
import { useState } from "react";
import { Avatar, Menu, menuItemClass } from "../primitives";
import { applyDensity, currentUserFullName, currentUserImage, getDensity } from "../../lib/ui-flag";
import { useExcomBranding } from "../../hooks/useBranding";

/**
 * Shell for marketing-only users (Excom Marketing without Excom Agent / Admin). No rail, inbox,
 * softphone or worklist: those belong to the inbox roles and their APIs refuse this role.
 */
export function MarketingShell() {
  return (
    <div className="h-full w-full flex flex-col bg-surface text-ink-1 overflow-hidden">
      <main className="flex-1 min-w-0 min-h-0 flex">
        <Outlet />
      </main>
    </div>
  );
}

/** Account menu in the Marketing header for marketing-only users: Desk (ads setup), density, sign out. */
export function MarketingAccountMenu() {
  const { branding } = useExcomBranding();
  const [density, setDensity] = useState(getDensity());
  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button type="button" className="flex items-center rounded-full hover:ring-2 hover:ring-border" title="Account" aria-label="Account">
          <Avatar name={currentUserFullName()} src={currentUserImage()} size={28} />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content side="bottom" align="end" sideOffset={6} className="z-50 min-w-[220px] rounded-lg border border-border bg-surface p-1 shadow-ex">
          <Menu.Label className="px-2 py-1.5 text-xs text-ink-3 truncate">{branding?.app_name || "Excom"} · {currentUserFullName()}</Menu.Label>
          <Menu.Item className={menuItemClass} onSelect={() => window.open("/app", "_blank", "noopener")}><ExternalLink />Open Desk (ads setup)</Menu.Item>
          <Menu.Item className={menuItemClass} onSelect={() => { const d = density === "compact" ? "comfortable" : "compact"; applyDensity(d); setDensity(d); }}>
            <Rows3 />Density: {density === "compact" ? "Compact" : "Comfortable"}
          </Menu.Item>
          <Menu.Separator className="my-1 h-px bg-border" />
          <Menu.Item className={menuItemClass} onSelect={() => { window.location.href = "/?cmd=web_logout"; }}><LogOut />Sign out</Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
