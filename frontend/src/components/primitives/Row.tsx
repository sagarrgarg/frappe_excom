import * as React from "react";
import { cn } from "../ui/utils";

interface RowProps extends React.ComponentProps<"div"> {
  selected?: boolean;
  /** Use dense row height (36/44) instead of list row (56/68). */
  dense?: boolean;
  /** Renders as a button-like element with keyboard support. */
  interactive?: boolean;
  /** 3px crayon left border (one accent per row). */
  accentBorder?: string;
}

/**
 * Row — `min-w-0` on the parent chain, `truncate` on the leaf (§2.5 rule 1).
 * Selection = surface-active, never a full-row crayon tint.
 */
export const Row = React.forwardRef<HTMLDivElement, RowProps>(
  ({ selected, dense, interactive = true, accentBorder, className, children, onClick, onKeyDown, ...rest }, ref) => (
    <div
      ref={ref}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-selected={selected}
      onClick={onClick}
      onKeyDown={(e) => {
        onKeyDown?.(e);
        if (interactive && !e.defaultPrevented && (e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
          e.preventDefault();
          (e.currentTarget as HTMLDivElement).click();
        }
      }}
      className={cn(
        "t2-host relative flex items-center gap-3 px-3 min-w-0 w-full text-left border-l-[3px] border-transparent",
        dense ? "h-row-dense" : "h-row-list",
        interactive && "cursor-pointer hover:bg-surface-hover focus-visible:outline-none focus-visible:bg-surface-hover",
        selected && "bg-surface-active hover:bg-surface-active",
        accentBorder,
        className
      )}
      {...rest}
    >
      {children}
    </div>
  )
);
Row.displayName = "Row";
