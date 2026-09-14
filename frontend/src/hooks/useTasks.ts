import { useCallback } from "react";
import {
  useFrappeCreateDoc,
  useFrappeGetDocList,
  useFrappePostCall,
  useFrappeUpdateDoc,
} from "frappe-react-sdk";
import { toast } from "sonner";
import type { RecordRef } from "./useRecordLinks";
import { toastError } from "../components/ErrorDialog";

export interface Task {
  name: string;
  description: string;
  status: "Open" | "Closed" | "Cancelled";
  date: string | null;
  priority: "High" | "Medium" | "Low";
  allocated_to: string | null;
  owner: string;
  creation: string;
  modified: string;
  /** From the Reminder pointing at this task, when somebody set a time. Not a ToDo field. */
  remind_at?: string;
}

interface Reminder {
  name: string;
  remind_at: string;
  reminder_docname: string;
}

/** Tasks tab: core ToDo with reference_type/reference_name → linked party. Inherits Desk assignment + notifications. */
export function useTasks(record: RecordRef | null) {
  const { data, isLoading, mutate } = useFrappeGetDocList<Task>(
    "ToDo",
    {
      fields: ["name", "description", "status", "date", "priority", "allocated_to", "owner", "creation", "modified"],
      filters: record ? [["reference_type", "=", record.doctype], ["reference_name", "=", record.name]] : [["name", "=", "__none__"]],
      orderBy: { field: "modified", order: "desc" },
      limit: 100,
    },
    record ? `todos-${record.doctype}-${record.name}` : null
  );
  const { createDoc, loading: creating } = useFrappeCreateDoc();
  const { updateDoc } = useFrappeUpdateDoc();
  // Frappe's own "remind me at", which is already scheduled and already fires. The due date stays
  // a date because that is what ToDo.date means to assignment rules and to every other app here.
  const { call: createReminder } = useFrappePostCall(
    "frappe.automation.doctype.reminder.reminder.create_new_reminder",
  );

  const rows = data ?? [];

  // Only the agent's own reminders come back — the doctype is owner-only, and its validate() puts
  // the session user on every one it creates, so there is nothing here to scope by hand.
  const { data: reminders } = useFrappeGetDocList<Reminder>(
    "Reminder",
    {
      fields: ["name", "remind_at", "reminder_docname"],
      filters: rows.length
        ? [["reminder_doctype", "=", "ToDo"], ["reminder_docname", "in", rows.map((t) => t.name)]]
        : [["name", "=", "__none__"]],
      limit: 200,
    },
    rows.length ? `reminders-${rows.map((t) => t.name).join(",")}` : null,
  );

  const byTask = new Map((reminders ?? []).map((r) => [r.reminder_docname, r.remind_at]));
  const tasks: Task[] = rows.map((t) => ({ ...t, remind_at: byTask.get(t.name) }));
  const open = tasks.filter((t) => t.status === "Open");

  const addTask = useCallback(
    async (
      description: string,
      date?: string,
      priority: Task["priority"] = "Medium",
      allocated_to?: string,
      time?: string,
    ) => {
      if (!record) return;
      try {
        const todo = await createDoc("ToDo", {
          description,
          reference_type: record.doctype,
          reference_name: record.name,
          status: "Open",
          priority,
          date: date || undefined,
          allocated_to: allocated_to || undefined,
        });

        // The task is saved either way. A reminder that cannot be set — a time already gone, most
        // often — must not read as the task having failed, so it is reported on its own.
        if (date && time) {
          try {
            await createReminder({
              remind_at: `${date} ${time}:00`,
              description: description.slice(0, 140),
              reminder_doctype: "ToDo",
              reminder_docname: (todo as any)?.name,
            });
            toast.success("Task added", { description: `You will be reminded at ${time}.` });
          } catch (err: any) {
            toast.success("Task added");
            toast.warning("The reminder was not set.", {
              description: err?.message?.includes("past")
                ? "That time has already passed today."
                : "The task is saved; only the reminder failed.",
            });
          }
        } else {
          toast.success("Task added");
        }
        await mutate();
      } catch (e: any) {
        toast.error(e?.message || "Failed to add task");
      }
    },
    [record, createDoc, createReminder, mutate]
  );

  const setStatus = useCallback(
    async (name: string, status: Task["status"]) => {
      try {
        await updateDoc("ToDo", name, { status });
        await mutate();
      } catch (err) {
        toastError(err, "Failed to update task");
      }
    },
    [updateDoc, mutate]
  );

  return { tasks, open, isLoading, creating, addTask, setStatus, refresh: mutate };
}
