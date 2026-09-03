import * as React from "react";
import * as DM from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal } from "lucide-react";
import { cn } from "../ui/utils";
import { Button } from "./Button";
import { Kbd } from "./Kbd";

export interface MenuItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
  shortcut?: string;
  danger?: boolean;
  disabled?: boolean;
  hint?: string;
  onSelect?: () => void;
}

export type MenuGroup = MenuItem[];

interface OverflowMenuProps {
  /** Groups are separated by a divider. */
  groups: MenuGroup[];
  trigger?: React.ReactNode;
  align?: "start" | "end";
  label?: string;
  size?: "icon" | "icon-sm";
  className?: string;
}

const itemCls =
  "flex items-center gap-2 rounded px-2 h-8 text-sm text-ink-1 outline-none cursor-default select-none min-w-0 data-[highlighted]:bg-surface-hover data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:text-ink-3 [&_svg]:shrink-0";

/** T3 overflow `⋯` — grouped, with shortcut hints. */
export function OverflowMenu({ groups, trigger, align = "end", label = "More", size = "icon", className }: OverflowMenuProps) {
  return (
    <DM.Root modal={false}>
      <DM.Trigger asChild>
        {trigger ?? (
          <Button variant="ghost" size={size} aria-label={label} title={label} className={className}>
            <MoreHorizontal />
          </Button>
        )}
      </DM.Trigger>
      <DM.Portal>
        <DM.Content
          align={align}
          sideOffset={4}
          collisionPadding={8}
          className="z-50 min-w-[200px] max-w-[min(92vw,320px)] rounded-lg border border-border bg-surface p-1 shadow-ex"
        >
          {groups.filter((g) => g.length).map((g, gi) => (
            <React.Fragment key={gi}>
              {gi > 0 && <DM.Separator className="my-1 h-px bg-border" />}
              {g.map((it) => (
                <DM.Item
                  key={it.id}
                  disabled={it.disabled}
                  onSelect={() => it.onSelect?.()}
                  className={cn(itemCls, it.danger && "text-crayon-rose-text [&_svg]:text-crayon-rose-text")}
                >
                  {it.icon}
                  <span className="truncate flex-1">{it.label}</span>
                  {it.hint && <span className="text-xs text-ink-3 truncate">{it.hint}</span>}
                  {it.shortcut && <Kbd>{it.shortcut}</Kbd>}
                </DM.Item>
              ))}
            </React.Fragment>
          ))}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

export const Menu = DM;
export const menuItemClass = itemCls;
