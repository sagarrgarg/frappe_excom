import { cn } from "../ui/utils";
import { Badge } from "./Badge";

export interface Segment<T extends string> {
  value: T;
  label: string;
  icon?: React.ReactNode;
  count?: number;
  disabled?: boolean;
  hint?: string;
}

/** Tabs that look like tabs on laptop, and a segmented control in narrow places. Height = --ex-tabs-h. */
export function SegmentedControl<T extends string>({ value, onChange, segments, className, variant = "tabs", ariaLabel }: { value: T; onChange: (v: T) => void; segments: Segment<T>[]; className?: string; variant?: "tabs" | "segmented"; ariaLabel?: string }) {
  return (
    <div role="tablist" aria-label={ariaLabel} className={cn("chip-row h-tabs-h shrink-0", variant === "segmented" && "bg-surface-sunken rounded-md p-0.5 h-8", className)}>
      {segments.map((s) => {
        const active = s.value === value;
        return (
          <button
            key={s.value}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={s.disabled}
            title={s.hint}
            onClick={() => onChange(s.value)}
            className={cn(
              "inline-flex items-center gap-1.5 text-sm font-medium px-2.5 h-full min-w-0 whitespace-nowrap disabled:opacity-40 [&_svg]:size-4 [&_svg]:shrink-0 outline-none",
              variant === "tabs"
                ? cn("border-b-2 -mb-px", active ? "border-crayon-blue-base text-ink-1" : "border-transparent text-ink-2 hover:text-ink-1")
                : cn("rounded", active ? "bg-surface text-ink-1 shadow-ex" : "text-ink-2 hover:text-ink-1")
            )}
          >
            {s.icon}
            <span className="truncate">{s.label}</span>
            {typeof s.count === "number" && s.count > 0 && <Badge accent={active ? "blue" : "neutral"} count={s.count} />}
          </button>
        );
      })}
    </div>
  );
}
