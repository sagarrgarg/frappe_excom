import * as React from "react";
import { X } from "lucide-react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../ui/utils";

export type Accent = "blue" | "green" | "amber" | "rose" | "violet" | "teal" | "plum" | "sand" | "neutral";

/** Accent → tint/text classes. Kept as a map so grep gates see no raw hex. */
export const ACCENT_CLASSES: Record<Accent, { tint: string; text: string; base: string; border: string }> = {
  blue: { tint: "bg-crayon-blue-tint", text: "text-crayon-blue-text", base: "bg-crayon-blue-base", border: "border-crayon-blue-base" },
  green: { tint: "bg-crayon-green-tint", text: "text-crayon-green-text", base: "bg-crayon-green-base", border: "border-crayon-green-base" },
  amber: { tint: "bg-crayon-amber-tint", text: "text-crayon-amber-text", base: "bg-crayon-amber-base", border: "border-crayon-amber-base" },
  rose: { tint: "bg-crayon-rose-tint", text: "text-crayon-rose-text", base: "bg-crayon-rose-base", border: "border-crayon-rose-base" },
  violet: { tint: "bg-crayon-violet-tint", text: "text-crayon-violet-text", base: "bg-crayon-violet-base", border: "border-crayon-violet-base" },
  teal: { tint: "bg-crayon-teal-tint", text: "text-crayon-teal-text", base: "bg-crayon-teal-base", border: "border-crayon-teal-base" },
  plum: { tint: "bg-crayon-plum-tint", text: "text-crayon-plum-text", base: "bg-crayon-plum-base", border: "border-crayon-plum-base" },
  sand: { tint: "bg-crayon-sand-tint", text: "text-crayon-sand-text", base: "bg-crayon-sand-base", border: "border-crayon-sand-base" },
  neutral: { tint: "bg-surface-sunken", text: "text-ink-2", base: "bg-ink-muted", border: "border-border-strong" },
};

const chipVariants = cva(
  "inline-flex items-center gap-1 rounded-full text-xs font-medium whitespace-nowrap max-w-full min-w-0 select-none [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      size: {
        sm: "h-5 px-1.5",
        md: "h-6 px-2",
        touch: "h-8 px-3",
      },
      interactive: {
        true: "cursor-pointer hover:brightness-95 focus-visible:outline-none",
        false: "",
      },
      selected: {
        true: "ring-1 ring-inset",
        false: "",
      },
    },
    defaultVariants: { size: "md", interactive: false, selected: false },
  }
);

export interface ChipProps
  extends Omit<React.ComponentProps<"span">, "children">,
    VariantProps<typeof chipVariants> {
  accent?: Accent;
  icon?: React.ReactNode;
  label: React.ReactNode;
  /** Numeric count rendered tabular. */
  count?: number;
  onRemove?: () => void;
  onClick?: React.MouseEventHandler<HTMLElement>;
  title?: string;
  /** Inline colour from data (e.g. tag.color). Applied as a dot, never a fill. */
  dotColor?: string;
}

/**
 * Chip — a crayon may fill a chip, a dot, a 3px border or a 1-line strip. Never a panel.
 * One accent per row; channel identity always carries icon + label.
 */
export function Chip({
  accent = "neutral", icon, label, count, onRemove, onClick, className, size, selected, dotColor, title, ...rest
}: ChipProps) {
  const a = ACCENT_CLASSES[accent];
  const interactive = Boolean(onClick);
  const Comp: any = interactive ? "button" : "span";
  return (
    <Comp
      type={interactive ? "button" : undefined}
      onClick={onClick}
      title={title}
      className={cn(
        chipVariants({ size, interactive, selected }),
        a.tint, a.text,
        selected && `ring-current`,
        className
      )}
      {...rest}
    >
      {dotColor && <span className="size-2 rounded-full shrink-0" style={{ backgroundColor: dotColor }} />}
      {icon}
      <span className="truncate">{label}</span>
      {typeof count === "number" && <span className="tabular-nums text-2xs">{count}</span>}
      {onRemove && (
        <button
          type="button"
          aria-label="Remove"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="ml-0.5 -mr-0.5 rounded-full hover:bg-black/10 p-px"
        >
          <X className="size-3" />
        </button>
      )}
    </Comp>
  );
}
