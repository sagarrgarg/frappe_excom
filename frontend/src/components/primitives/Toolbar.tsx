import * as React from "react";
import { cn } from "../ui/utils";

/**
 * Toolbar — a fixed-height strip. Children that hold text must be min-w-0.
 * Action clusters collapse into OverflowMenu below container width 1100 (see useContainerWidth).
 */
export const Toolbar = React.forwardRef<HTMLDivElement, React.ComponentProps<"div"> & { height?: string }>(
  function Toolbar({ className, children, height = "h-header-h", ...rest }, ref) {
    return (
      <div
        ref={ref}
        className={cn("flex items-center gap-2 px-3 shrink-0 min-w-0 border-b border-border bg-surface", height, className)}
        {...rest}
      >
        {children}
      </div>
    );
  }
);

/** Measures the width of a container so layout decisions use container, not viewport (§2.5 rule 3). */
export function useContainerWidth<T extends HTMLElement>() {
  const ref = React.useRef<T>(null);
  const [width, setWidth] = React.useState(0);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}
