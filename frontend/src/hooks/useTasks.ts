import { useCallback } from "react";
import { useFrappeCreateDoc, useFrappeGetDocList, useFrappeUpdateDoc } from "frappe-react-sdk";
import { toast } from "sonner";
import type { RecordRef } from "./useRecordLinks";

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

  const tasks = data ?? [];
  const open = tasks.filter((t) => t.status === "Open");

  const addTask = useCallback(
    async (description: string, date?: string, priority: Task["priority"] = "Medium", allocated_to?: string) => {
      if (!record) return;
      try {
        await createDoc("ToDo", {
          description,
          reference_type: record.doctype,
          reference_name: record.name,
          status: "Open",
          priority,
          date: date || undefined,
          allocated_to: allocated_to || undefined,
        });
        toast.success("Task added");
        await mutate();
      } catch (e: any) {
        toast.error(e?.message || "Failed to add task");
      }
    },
    [record, createDoc, mutate]
  );

  const setStatus = useCallback(
    async (name: string, status: Task["status"]) => {
      try {
        await updateDoc("ToDo", name, { status });
        await mutate();
      } catch {
        toast.error("Failed to update task");
      }
    },
    [updateDoc, mutate]
  );

  return { tasks, open, isLoading, creating, addTask, setStatus, refresh: mutate };
}
