import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, MessageCircle, Mail, ListTodo, PhoneMissed, MessageSquare } from "lucide-react";
import { Menu, menuItemClass } from "../primitives";
import { cn } from "../ui/utils";
import { useWorklist, type Worklist } from "../../hooks/useWorklist";

/**
 * What is waiting, kept apart by kind.
 *
 * Excom had no such surface at all — an unread count on the inbox and nothing else. A task fell due
 * and said nothing; a call was missed and only the Calls page knew; WhatsApp and email were added
 * together into one number that told you neither. Four people waiting on WhatsApp and four
 * newsletters sitting unread are not the same situation, and one figure cannot say which you have.
 *
 * Frappe's own notifications go to the Desk bell, which an agent working in Excom never opens, so
 * anything that matters here has to be said here.
 */

const SEEN_KEY = "excom_attention_said";

interface Stream {
  key: string;
  label: string;
  count: number;
  icon: React.ReactNode;
  to: string;
  hint?: string;
}

function streamsOf(w: Worklist): Stream[] {
  return [
    {
      key: "whatsapp",
      label: "WhatsApp",
      count: w.whatsapp.threads,
      icon: <MessageCircle className="size-4" />,
      to: "/inbox?channel=whatsapp",
      hint: w.whatsapp.messages > w.whatsapp.threads ? `${w.whatsapp.messages} messages` : undefined,
    },
    {
      key: "email",
      label: "Email",
      count: w.email.threads,
      icon: <Mail className="size-4" />,
      to: "/inbox?channel=email",
      hint: w.email.messages > w.email.threads ? `${w.email.messages} messages` : undefined,
    },
    {
      key: "other",
      label: "Other channels",
      count: w.other.threads,
      icon: <MessageSquare className="size-4" />,
      to: "/inbox",
    },
    {
      key: "tasks",
      label: "Overdue tasks",
      count: w.tasks.count,
      icon: <ListTodo className="size-4" />,
      to: "/tasks",
    },
    {
      key: "calls",
      label: "Missed calls",
      count: w.calls.count,
      icon: <PhoneMissed className="size-4" />,
      to: "/calls",
    },
  ];
}

/** Announced already in this browser, so a reload does not repeat yesterday's news. */
function seen(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}");
  } catch {
    return {};
  }
}

function remember(counts: Record<string, number>) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(counts));
  } catch {
    /* a private window cannot remember; saying it twice beats not saying it */
  }
}

export function AttentionBell({ compact = false }: { compact?: boolean }) {
  const navigate = useNavigate();
  const { worklist, ready } = useWorklist();
  const saidRef = useRef<Record<string, number>>(seen());

  const streams = streamsOf(worklist);
  const waiting = streams.filter((s) => s.count > 0);
  const total = worklist.needs_attention;

  // Say the kinds that grew, and say what they are. "3 tasks are overdue" is actionable in a way
  // that a bare number on a bell is not.
  useEffect(() => {
    if (!ready) return;
    const now: Record<string, number> = {};
    const grew: string[] = [];
    for (const s of streams) {
      now[s.key] = s.count;
      const before = saidRef.current[s.key] ?? 0;
      if (s.count > before) grew.push(`${s.count} ${s.label.toLowerCase()}`);
    }
    saidRef.current = now;
    remember(now);

    if (!grew.length) return;
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    if (document.hasFocus()) return; // they are already here; the badge is enough
    try {
      new Notification("Excom", {
        body: grew.join(" · ") + " waiting",
        icon: "/assets/excom/excom/manifest/android-chrome-192x192.png",
        tag: "excom-attention",
      });
    } catch {
      /* not supported in this context */
    }
  }, [ready, worklist]);

  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button
          type="button"
          title={total ? `${total} waiting` : "Nothing waiting"}
          aria-label={total ? `${total} things waiting` : "Nothing waiting"}
          className="flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-2.5 text-sm text-ink-2 hover:bg-surface-hover hover:text-ink-1"
        >
          <span className="relative inline-flex shrink-0">
            <Bell className="size-5" />
            {total > 0 && (
              <span
                className="absolute -right-1 -top-1 min-w-[1rem] rounded-full bg-crayon-rose-base px-1 text-[0.6rem] font-semibold leading-4 text-white ring-2 ring-surface tabular-nums"
                aria-hidden
              >
                {total > 99 ? "99+" : total}
              </span>
            )}
          </span>
          <span className={cn("flex-1 truncate text-left", compact ? "w-0 opacity-0" : "opacity-100")}>
            {total ? `${total} waiting` : "Nothing waiting"}
          </span>
        </button>
      </Menu.Trigger>

      <Menu.Portal>
        <Menu.Content
          side="right"
          align="end"
          sideOffset={6}
          collisionPadding={8}
          className="z-50 min-w-[260px] max-w-[min(92vw,320px)] rounded-lg border border-border bg-surface p-1 shadow-ex"
        >
          {waiting.length === 0 ? (
            <p className="px-2.5 py-3 text-sm text-ink-3">
              Nothing is waiting. Anything overdue will appear here.
            </p>
          ) : (
            waiting.map((s) => (
              <Menu.Item
                key={s.key}
                onSelect={() => navigate(s.to)}
                className={menuItemClass}
              >
                {s.icon}
                <span className="truncate flex-1">{s.label}</span>
                {s.hint && <span className="text-xs text-ink-3 truncate">{s.hint}</span>}
                <span className="text-sm font-semibold tabular-nums">{s.count}</span>
              </Menu.Item>
            ))
          )}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
