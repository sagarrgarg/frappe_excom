import { ChevronDown } from "lucide-react";
import { Menu, menuItemClass, Badge } from "../primitives";
import { useInbox } from "../shell/InboxProvider";
import { viewPredicate } from "../../lib/views";
import { cn } from "../ui/utils";

/**
 * Saved-view picker. Compact dropdown (Gmail-style) so it takes one 32px line, with live counts.
 */
export function ViewList() {
  const { views, viewId, setView, allContacts, contacts } = useInbox();
  const current = views.find((v) => v.id === viewId) || views[5];
  const countFor = (id: string) => {
    const v = views.find((x) => x.id === id);
    if (!v) return 0;
    if (v.predicate) return allContacts.filter(viewPredicate(v)).length;
    return 0;
  };
  return (
    <div className="shrink-0 flex items-center gap-1 px-2 h-8 border-b border-border bg-surface-sunken min-w-0">
      <Menu.Root modal={false}>
        <Menu.Trigger asChild>
          <button type="button" className="flex items-center gap-1 h-7 px-1.5 rounded text-sm font-semibold text-ink-1 hover:bg-surface-hover min-w-0 max-w-full" aria-label="Change view">
            <span className="truncate">{current.label}</span>
            <ChevronDown className="size-4 text-ink-3 shrink-0" />
          </button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="start" sideOffset={4} className="z-50 min-w-[220px] rounded-lg border border-border bg-surface p-1 shadow-ex">
            {views.map((v, i) => {
              const n = countFor(v.id);
              return (
                <Menu.Item key={v.id} className={cn(menuItemClass, v.id === viewId && "bg-surface-active")} onSelect={() => setView(v.id)}>
                  {i === 8 && <Menu.Separator className="my-1 h-px bg-border" />}
                  <span className="truncate flex-1">{v.label}</span>
                  {v.hint && <span className="text-xs text-ink-3 truncate max-w-[120px]">{v.hint}</span>}
                  {v.predicate && n > 0 && <Badge accent={v.predicate === "sla" ? "rose" : v.predicate === "today" ? "amber" : "neutral"} count={n} />}
                </Menu.Item>
              );
            })}
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
      <span className="text-xs text-ink-3 tabular-nums ml-auto shrink-0">{contacts.length}</span>
    </div>
  );
}
