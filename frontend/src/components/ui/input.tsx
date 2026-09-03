import * as React from "react";
import { cn } from "./utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(
        "flex h-9 w-full min-w-0 rounded-md border border-border-strong bg-surface-sunken px-3 py-1 text-base text-ink-1 placeholder:text-ink-3 transition-colors outline-none disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        "focus-visible:border-crayon-blue-base/40 focus-visible:ring-1 focus-visible:ring-crayon-blue-base",
        className
      )}
      {...props}
    />
  );
}

export { Input };
