import * as React from "react";
import { cn } from "../ui/utils";

interface DrawerProps {
  open: boolean;
  /** Persistent drawer (wide) is part of layout; push drawer (laptop) pushes the record pane. */
  children: React.ReactNode;
  className?: string;
  width?: string;
}

/**
 * Drawer — non-modal side panel. At `wide` it is always open; at `laptop` `⌘.` toggles it
 * and it pushes content (no overlay). On tablet/phone, DetailsDrawer renders a Sheet instead.
 */
export function Drawer({ open, children, className, width = "w-details" }: DrawerProps) {
  if (!open) return null;
  return (
    <aside className={cn("shrink-0 h-full min-h-0 border-l border-border bg-surface flex flex-col overflow-hidden", width, className)}>
      {children}
    </aside>
  );
}
