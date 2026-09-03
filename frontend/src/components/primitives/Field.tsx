import * as React from "react";
import { cn } from "../ui/utils";

const inputCls =
  "flex w-full min-w-0 h-8 rounded-md border border-border-strong bg-surface px-2.5 text-sm text-ink-1 placeholder:text-ink-3 outline-none transition-colors focus-visible:border-crayon-blue-base focus-visible:ring-1 focus-visible:ring-crayon-blue-base disabled:opacity-50";

export const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn(inputCls, className)} {...props} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(inputCls, "h-auto min-h-[72px] py-2 resize-y", className)} {...props} />
));
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.ComponentProps<"select">>(({ className, children, ...props }, ref) => (
  <select ref={ref} className={cn(inputCls, "appearance-none pr-7 bg-no-repeat bg-[right_8px_center]", className)} {...props}>
    {children}
  </select>
));
Select.displayName = "Select";

/** Label + control + hint. Labels are 12px ink-3, never uppercase-tracked. */
export function Field({ label, hint, required, children, className, htmlFor }: { label: React.ReactNode; hint?: React.ReactNode; required?: boolean; children: React.ReactNode; className?: string; htmlFor?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <label htmlFor={htmlFor} className="block text-xs text-ink-3 mb-1">
        {label}
        {required && <span className="text-crayon-rose-text ml-0.5">*</span>}
      </label>
      {children}
      {hint && <p className="text-xs text-ink-3 mt-1">{hint}</p>}
    </div>
  );
}

export const inputClass = inputCls;
