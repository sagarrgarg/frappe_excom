import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button, Toolbar } from "../primitives";
import { useInbox } from "./InboxProvider";
import { cn } from "../ui/utils";

/**
 * Frame for admin pages: 48px header with back (phone/tablet), title, actions; scrollable body.
 * Content max-width caps at 1200px so 1920px screens don't produce 1200px line lengths.
 */
export function PageFrame({ title, icon, actions, children, className, wide }: { title: React.ReactNode; icon?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; wide?: boolean }) {
  const navigate = useNavigate();
  const { bp } = useInbox();
  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col bg-surface">
      <Toolbar>
        {(bp === "phone" || bp === "tablet") && (
          <Button variant="ghost" size="icon" aria-label="Back" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate("/inbox"))}><ArrowLeft /></Button>
        )}
        {icon && <span className="text-ink-3 [&>svg]:size-5 shrink-0">{icon}</span>}
        <h1 className="text-md text-ink-1 truncate flex-1 min-w-0">{title}</h1>
        <div className="flex items-center gap-1.5 shrink-0 min-w-0">{actions}</div>
      </Toolbar>
      <div className={cn("flex-1 min-h-0 overflow-y-auto", className)}>
        <div className={cn("mx-auto w-full p-3", wide ? "max-w-[1400px]" : "max-w-[1200px]")}>{children}</div>
      </div>
    </div>
  );
}
