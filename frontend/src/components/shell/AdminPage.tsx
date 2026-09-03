import { ArrowLeft } from "lucide-react";
import { Button, Toolbar } from "../primitives";
import { cn } from "../ui/utils";

interface AdminPageProps {
  title: React.ReactNode;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  /** Below-header strip (filters/tabs). Stays visible while the body scrolls. */
  toolbar?: React.ReactNode;
  onBack?: () => void;
  /** Mounted inside the P1 shell (rail present): back only shows below laptop. Legacy: always. */
  embedded?: boolean;
  /** Skip the max-width + padding wrapper (full-bleed lists/tables). */
  bleed?: boolean;
  wide?: boolean;
  children: React.ReactNode;
  className?: string;
}

/**
 * Frame for the seven admin pages (W11). 48px header, optional toolbar strip, scrolling body.
 * Works in both trees — embedded (P1 shell) or standalone (legacy). Content max-width 1200 by default.
 */
export function AdminPage({ title, icon, actions, toolbar, onBack, embedded, bleed, wide, children, className }: AdminPageProps) {
  return (
    <div className={cn("flex-1 h-full w-full min-w-0 min-h-0 flex flex-col bg-surface", className)}>
      <Toolbar>
        {onBack && (
          <Button variant="ghost" size="icon" aria-label="Back" onClick={onBack} className={cn(embedded && "laptop:hidden")}><ArrowLeft /></Button>
        )}
        {icon && <span className="text-ink-3 [&>svg]:size-5 shrink-0">{icon}</span>}
        <h1 className="text-md text-ink-1 truncate flex-1 min-w-0">{title}</h1>
        {actions && <div className="flex items-center gap-1.5 shrink-0 min-w-0 max-w-[70%]">{actions}</div>}
      </Toolbar>
      {toolbar && <div className="shrink-0 border-b border-border bg-surface-sunken px-3 py-1.5 min-w-0">{toolbar}</div>}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {bleed ? children : <div className={cn("mx-auto w-full p-3", wide ? "max-w-[1400px]" : "max-w-[1200px]")}>{children}</div>}
      </div>
    </div>
  );
}

/** Responsive data table: a real <table> from tablet up, stacked cards on phone. */
export function DataTable<T>({ rows, columns, keyOf, onRowClick, empty }: {
  rows: T[];
  columns: { key: string; label: string; render: (r: T) => React.ReactNode; align?: "left" | "right"; className?: string; primary?: boolean }[];
  keyOf: (r: T) => string;
  onRowClick?: (r: T) => void;
  empty?: React.ReactNode;
}) {
  if (rows.length === 0) return <>{empty}</>;
  return (
    <>
      <div className="hidden tablet:block overflow-x-auto">
        <table className="w-full text-sm tabular-nums">
          <thead>
            <tr className="border-b border-border text-xs text-ink-3">
              {columns.map((c) => <th key={c.key} className={cn("px-3 h-9 font-medium whitespace-nowrap", c.align === "right" ? "text-right" : "text-left", c.className)}>{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={keyOf(r)} onClick={onRowClick ? () => onRowClick(r) : undefined} className={cn("border-b border-border", onRowClick && "cursor-pointer hover:bg-surface-hover")}>
                {columns.map((c) => <td key={c.key} className={cn("px-3 h-row-dense align-middle max-w-[320px] truncate", c.align === "right" && "text-right", c.className)}>{c.render(r)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="tablet:hidden divide-y divide-border">
        {rows.map((r) => (
          <li key={keyOf(r)} onClick={onRowClick ? () => onRowClick(r) : undefined} className={cn("px-3 py-2.5 min-w-0 space-y-1", onRowClick && "cursor-pointer active:bg-surface-hover")}>
            {columns.map((c) => (
              <div key={c.key} className={cn("flex items-center gap-2 min-w-0 text-sm", c.primary ? "text-ink-1 font-medium" : "")}>
                {!c.primary && <span className="text-xs text-ink-3 w-20 shrink-0">{c.label}</span>}
                <span className="truncate min-w-0 flex-1">{c.render(r)}</span>
              </div>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}
