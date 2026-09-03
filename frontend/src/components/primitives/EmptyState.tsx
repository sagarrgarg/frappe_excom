import * as React from "react";
import { cn } from "../ui/utils";

export function EmptyState({ icon, title, hint, action, className, compact }: { icon?: React.ReactNode; title: string; hint?: string; action?: React.ReactNode; className?: string; compact?: boolean }) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center px-6", compact ? "py-6" : "py-12", className)}>
      {icon && <div className="size-10 rounded-full bg-surface-sunken text-ink-3 flex items-center justify-center mb-3 [&_svg]:size-5">{icon}</div>}
      <p className="text-sm font-medium text-ink-1">{title}</p>
      {hint && <p className="text-xs text-ink-3 mt-1 max-w-xs">{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
