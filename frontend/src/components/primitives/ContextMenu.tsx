import * as React from "react";
import * as CM from "@radix-ui/react-context-menu";
import { cn } from "../ui/utils";
import { Kbd } from "./Kbd";
import { menuItemClass, type MenuGroup } from "./OverflowMenu";

/**
 * Custom right-click menu (long-press on touch — Radix handles the 700 ms hold) with the same
 * groups/items as the ⋯ menu, so the two never drift apart. Wrap a single ref-forwarding element.
 */
export function ContextMenu({ groups, children, disabled }: { groups: MenuGroup[]; children: React.ReactElement; disabled?: boolean }) {
  return (
    <CM.Root modal={false}>
      <CM.Trigger asChild disabled={disabled}>{children}</CM.Trigger>
      <CM.Portal>
        <CM.Content collisionPadding={8} className="z-50 min-w-[200px] max-w-[min(92vw,320px)] rounded-lg border border-border bg-surface p-1 shadow-ex">
          {groups.filter((g) => g.length).map((g, gi) => (
            <React.Fragment key={gi}>
              {gi > 0 && <CM.Separator className="my-1 h-px bg-border" />}
              {g.map((it) => (
                <CM.Item key={it.id} disabled={it.disabled} onSelect={() => it.onSelect?.()} className={cn(menuItemClass, it.danger && "text-crayon-rose-text [&_svg]:text-crayon-rose-text")}>
                  {it.icon}
                  <span className="truncate flex-1">{it.label}</span>
                  {it.hint && <span className="text-xs text-ink-3 truncate">{it.hint}</span>}
                  {it.shortcut && <Kbd>{it.shortcut}</Kbd>}
                </CM.Item>
              ))}
            </React.Fragment>
          ))}
        </CM.Content>
      </CM.Portal>
    </CM.Root>
  );
}
