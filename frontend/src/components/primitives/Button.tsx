import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../ui/utils";

/**
 * Token-based button. Primary is a flat crayon-blue fill (no gradient, UX-001 §2.2).
 * Minimum hit target follows --ex-hit-min (32 desktop / 44 touch).
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 shrink-0 outline-none select-none min-w-0",
  {
    variants: {
      variant: {
        primary: "bg-crayon-blue-base text-white hover:bg-crayon-blue-text",
        default: "bg-surface border border-border-strong text-ink-1 hover:bg-surface-hover",
        subtle: "bg-surface-sunken text-ink-1 hover:bg-surface-hover",
        ghost: "text-ink-2 hover:bg-surface-hover hover:text-ink-1",
        danger: "bg-crayon-rose-tint text-crayon-rose-text border border-crayon-rose-base/40 hover:bg-crayon-rose-base hover:text-white",
        link: "text-crayon-blue-text underline-offset-4 hover:underline px-0",
      },
      size: {
        sm: "h-7 px-2 text-xs [&_svg]:size-3.5",
        md: "h-8 px-3 [&_svg]:size-4",
        lg: "h-9 px-4 text-base [&_svg]:size-4",
        icon: "h-8 w-8 [&_svg]:size-4",
        "icon-sm": "h-7 w-7 [&_svg]:size-3.5",
        touch: "h-hit min-w-hit px-3 [&_svg]:size-4",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  }
);

export type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : type ?? "button"}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { buttonVariants };
