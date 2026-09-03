import * as React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "../ui/utils";
import { Button } from "./Button";

interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: React.ReactNode;
  description?: string;
  /** phone: bottom sheet; tablet+: right sheet. Use side="bottom" to force. */
  side?: "auto" | "bottom" | "right";
  width?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}

/**
 * Sheet — modal surface used on phone/tablet for details, pickers and dialogs.
 * Bottom on phone, right on tablet+ (side="auto").
 */
export function Sheet({ open, onOpenChange, title, description, side = "auto", width = "w-[420px]", children, footer, className }: SheetProps) {
  const sideCls =
    side === "bottom"
      ? "inset-x-0 bottom-0 max-h-[92vh] rounded-t-xl"
      : side === "right"
      ? cn("inset-y-0 right-0 max-w-[100vw]", width)
      : cn("inset-x-0 bottom-0 max-h-[92vh] rounded-t-xl tablet:inset-x-auto tablet:inset-y-0 tablet:right-0 tablet:max-h-none tablet:rounded-none tablet:max-w-[100vw]", `tablet:${width}`);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink-1/40" />
        <Dialog.Content
          className={cn("fixed z-50 bg-surface border border-border flex flex-col outline-none shadow-ex", sideCls, className)}
        >
          <div className="flex items-center gap-2 px-3 h-header-h border-b border-border shrink-0 min-w-0 safe-area-top">
            <Dialog.Title className="text-md text-ink-1 truncate flex-1 min-w-0">{title}</Dialog.Title>
            {description ? <Dialog.Description className="sr-only">{description}</Dialog.Description> : <Dialog.Description className="sr-only">Dialog</Dialog.Description>}
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close"><X /></Button>
            </Dialog.Close>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
          {footer && <div className="shrink-0 border-t border-border px-3 py-2 flex items-center justify-end gap-2 safe-area-bottom">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Centered modal for confirmations and small forms. */
export function Modal({ open, onOpenChange, title, description, children, footer, className, width = "max-w-md" }: Omit<SheetProps, "side" | "width"> & { width?: string }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink-1/40" />
        <Dialog.Content
          className={cn(
            "fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[calc(100vw-24px)] max-h-[90vh] bg-surface border border-border rounded-xl flex flex-col outline-none shadow-ex",
            width,
            className
          )}
        >
          <div className="flex items-center gap-2 px-4 h-header-h border-b border-border shrink-0 min-w-0">
            <Dialog.Title className="text-md text-ink-1 truncate flex-1 min-w-0">{title}</Dialog.Title>
            <Dialog.Description className="sr-only">{description || "Dialog"}</Dialog.Description>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close"><X /></Button>
            </Dialog.Close>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto p-4">{children}</div>
          {footer && <div className="shrink-0 border-t border-border px-4 py-3 flex items-center justify-end gap-2">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
