import { useEffect, useRef } from "react";
import { useFrappeGetDocList } from "frappe-react-sdk";
import { toast } from "sonner";

/** A reminder the agent set on one of their own tasks. */
interface DueReminder {
  name: string;
  description: string;
  remind_at: string;
  reminder_docname: string;
}

const SEEN_KEY = "excom_reminders_said";
const POLL_MS = 60_000;

/** Reminders already announced in this browser, so a reload does not repeat them. */
function seen(): string[] {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
  } catch {
    return [];
  }
}

function remember(name: string) {
  try {
    // Trimmed, because this only exists to stop a repeat within the day.
    localStorage.setItem(SEEN_KEY, JSON.stringify([...seen(), name].slice(-100)));
  } catch {
    /* a private window cannot remember; announcing twice is better than not at all */
  }
}

/**
 * Say a task reminder when its time arrives, in Excom.
 *
 * Frappe's own sweep runs every fifteen minutes and writes to the Desk bell. This one asks every
 * minute so the time an agent chose means roughly that time, and says it where they are working.
 */
export function useTaskReminders() {
  const saidRef = useRef<Set<string>>(new Set(seen()));

  const { data, mutate } = useFrappeGetDocList<DueReminder>(
    "Reminder",
    {
      // Owner-only doctype, and its validate() stamps the session user on everything it creates,
      // so this is already only the agent's own.
      fields: ["name", "description", "remind_at", "reminder_docname"],
      filters: [["reminder_doctype", "=", "ToDo"]],
      orderBy: { field: "remind_at", order: "desc" },
      limit: 50,
    },
    "excom-task-reminders",
  );

  useEffect(() => {
    const id = setInterval(() => void mutate(), POLL_MS);
    return () => clearInterval(id);
  }, [mutate]);

  useEffect(() => {
    const now = Date.now();
    for (const r of data ?? []) {
      if (saidRef.current.has(r.name)) continue;
      const due = new Date(r.remind_at.replace(" ", "T")).getTime();
      // Due, and not so long ago that saying it now would only confuse.
      if (Number.isNaN(due) || due > now || now - due > 6 * 60 * 60 * 1000) continue;

      saidRef.current.add(r.name);
      remember(r.name);
      toast("Task reminder", { description: r.description, duration: 15_000 });
      if ("Notification" in window && Notification.permission === "granted" && !document.hasFocus()) {
        try {
          new Notification("Task reminder", {
            body: r.description,
            icon: "/assets/excom/excom/manifest/android-chrome-192x192.png",
            tag: `excom-reminder-${r.name}`,
          });
        } catch {
          /* not supported in this context */
        }
      }
    }
  }, [data]);
}
