import { cn } from "../ui/utils";

export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center h-5 px-1.5 rounded border border-border-strong bg-surface-sunken text-xs text-ink-3 font-sans tabular-nums shrink-0",
        className
      )}
    >
      {children}
    </kbd>
  );
}
