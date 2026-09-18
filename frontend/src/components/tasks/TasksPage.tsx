import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Circle, ListTodo, Loader2, RefreshCw, AlertTriangle, ExternalLink } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { useFrappeUpdateDoc } from "frappe-react-sdk";
import { toast } from "sonner";
import { PageFrame } from "../shell/PageFrame";
import { Button, Chip, EmptyState, SegmentedControl } from "../primitives";
import { toastError } from "../ErrorDialog";
import { cn } from "../ui/utils";

/**
 * Tasks — everything this agent has promised, in one place.
 *
 * A task was only ever reachable from inside the conversation it hung off, so an agent who could
 * not remember which customer it was about had no way to find it at all. Overdue leads first,
 * because that is the list somebody is waiting on.
 */

type View = "overdue" | "today" | "upcoming" | "open" | "done";

interface Task {
  name: string;
  description: string;
  status: string;
  date: string | null;
  priority: "High" | "Medium" | "Low";
  allocated_to: string | null;
  reference_type?: string;
  reference_name?: string;
  reference_label?: string;
  omni_identity?: string | null;
}

export function TasksPage() {
  const [view, setView] = useState<View>("overdue");
  const navigate = useNavigate();

  const { data, isLoading, mutate } = useFrappeGetCall<{ message: Task[] }>(
    "excom.excom.api.worklist.get_my_tasks",
    { view, limit: 200 },
    "my-tasks:" + view,
    { revalidateOnFocus: false },
  );
  const rows = data?.message ?? [];

  // The badge counts what is late, not the view being shown, so switching away does not hide it.
  const { data: overdueData, mutate: mutateOverdue } = useFrappeGetCall<{ message: Task[] }>(
    "excom.excom.api.worklist.get_my_tasks",
    { view: "overdue", limit: 200 },
    "my-tasks:overdue",
    { revalidateOnFocus: false },
  );
  const overdueCount = (overdueData?.message ?? []).length;

  const { updateDoc } = useFrappeUpdateDoc();

  const refresh = () => {
    void mutate();
    void mutateOverdue();
  };

  const setStatus = async (name: string, status: "Open" | "Closed") => {
    try {
      await updateDoc("ToDo", name, { status });
      refresh();
    } catch (err) {
      toastError(err, "The task could not be updated");
    }
  };

  return (
    <PageFrame
      title="Tasks"
      icon={<ListTodo />}
      actions={
        <>
          <SegmentedControl<View>
            value={view}
            onChange={setView}
            ariaLabel="Which tasks to show"
            segments={[
              { value: "overdue", label: "Overdue", count: overdueCount || undefined },
              { value: "today", label: "Today" },
              { value: "upcoming", label: "Upcoming" },
              { value: "open", label: "All open" },
              { value: "done", label: "Done" },
            ]}
          />
          <Button variant="ghost" size="icon" aria-label="Refresh" onClick={refresh}>
            <RefreshCw className={isLoading ? "animate-spin" : ""} />
          </Button>
        </>
      }
      className="!p-0"
    >
      {isLoading && rows.length === 0 ? (
        <p className="p-3 text-sm text-ink-3">Loading…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<ListTodo />}
          title={EMPTY[view].title}
          hint={EMPTY[view].hint}
        />
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((t) => (
            <Row
              key={t.name}
              task={t}
              onToggle={() => setStatus(t.name, t.status === "Open" ? "Closed" : "Open")}
              onOpen={t.omni_identity ? () => navigate("/t/" + t.omni_identity) : undefined}
            />
          ))}
        </ul>
      )}
    </PageFrame>
  );
}

const EMPTY: Record<View, { title: string; hint: string }> = {
  overdue: { title: "Nothing overdue", hint: "Tasks past their day appear here." },
  today: { title: "Nothing due today", hint: "Tasks you set for today appear here." },
  upcoming: { title: "Nothing coming up", hint: "Tasks with a day still ahead appear here." },
  open: { title: "No open tasks", hint: "Add one from the Tasks tab of any conversation." },
  done: { title: "Nothing finished yet", hint: "Completed tasks are kept here." },
};

function Row({
  task,
  onToggle,
  onOpen,
}: {
  task: Task;
  onToggle: () => void;
  onOpen?: () => void;
}) {
  const closed = task.status !== "Open";
  const overdue = !closed && task.date && new Date(task.date) < new Date(new Date().toDateString());

  return (
    <li className="flex items-start gap-3 px-3 py-2.5 hover:bg-surface-hover min-w-0">
      <button
        type="button"
        aria-label={closed ? "Reopen this task" : "Mark this task done"}
        onClick={onToggle}
        className="mt-0.5 shrink-0 text-ink-3 hover:text-crayon-green-text"
      >
        {closed ? (
          <CheckCircle2 className="size-4 text-crayon-green-base" />
        ) : (
          <Circle className="size-4" />
        )}
      </button>

      <div className="flex-1 min-w-0">
        <p className={cn("text-sm text-ink-1 break-words", closed && "line-through text-ink-3")}>
          {task.description || "(no description)"}
        </p>
        <div className="flex items-center gap-1.5 mt-0.5 text-xs text-ink-3 flex-wrap min-w-0">
          {task.date && (
            <span className={cn("tabular-nums", overdue && "text-crayon-rose-text font-medium")}>
              {overdue && <AlertTriangle className="inline size-3 mr-0.5 -mt-0.5" />}
              {overdue ? "Overdue · " : "Due "}
              {task.date}
            </span>
          )}
          {task.priority === "High" && <Chip size="sm" accent="rose" label="High" />}
          {/* Which customer this is about, so the list reads without opening every row. */}
          {task.reference_label && (
            onOpen ? (
              <button
                type="button"
                onClick={onOpen}
                className="inline-flex items-center gap-0.5 truncate max-w-[16rem] hover:text-ink-1 underline decoration-dotted underline-offset-2"
                title="Open this conversation"
              >
                <ExternalLink className="size-3 shrink-0" />
                {task.reference_label}
              </button>
            ) : (
              <span className="truncate max-w-[16rem]">{task.reference_label}</span>
            )
          )}
        </div>
      </div>
    </li>
  );
}
