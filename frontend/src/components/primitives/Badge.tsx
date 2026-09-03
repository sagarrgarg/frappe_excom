import * as React from "react";
import { cn } from "../ui/utils";
import { ACCENT_CLASSES, type Accent } from "./Chip";

interface BadgeProps extends React.ComponentProps<"span"> {
  accent?: Accent;
  /** Solid fill (unread counts). Otherwise tinted. */
  solid?: boolean;
  count?: number;
  max?: number;
}

/** Numeric badge — the only place 11px text is allowed (UX-001 §2.3). */
export function Badge({ accent = "blue", solid = false, count, max = 99, className, children, ...rest }: BadgeProps) {
  const a = ACCENT_CLASSES[accent];
  const text = typeof count === "number" ? (count > max ? `${max}+` : String(count)) : children;
  return (
    <span
      data-counter
      className={cn(
        "inline-flex items-center justify-center rounded-full text-2xs tabular-nums px-1.5 min-w-[18px] h-[18px] leading-none shrink-0",
        solid ? cn(a.base, "text-white") : cn(a.tint, a.text),
        className
      )}
      {...rest}
    >
      {text}
    </span>
  );
}
