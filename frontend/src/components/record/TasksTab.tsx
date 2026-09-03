import { useState } from "react";
import { CheckCircle2, Circle, Plus, Loader2, ListTodo, ExternalLink } from "lucide-react";
import { useTasks, type Task } from "../../hooks/useTasks";
import type { RecordRef } from "../../hooks/useRecordLinks";
import { deskUrl } from "../../hooks/useRecordLinks";
import { Button, Input, EmptyState, Chip, Select } from "../primitives";
import { cn } from "../ui/utils";

function stripHtml(s: string) { return (s || "").replace(/<[^>]+>/g, "").trim(); }

/** Tasks tab — core ToDo against the linked party (E7). Add inline; toggle done. */
export function TasksTab({ record }: { record: RecordRef | null }) {
  const { tasks, isLoading, creating, addTask, setStatus } = useTasks(record);
  const [text, setText] = useState("");
  const [date, setDate] = useState("");
  const [priority, setPriority] = useState<Task["priority"]>("Medium");
  const [showDone, setShowDone] = useState(false);
  const open = tasks.filter((t) => t.status === "Open");
  const done = tasks.filter((t) => t.status !== "Open");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || !record) return;
    await addTask(text.trim(), date, priority);
    setText(""); setDate("");
  };

  return (
    <div className="flex flex-col min-h-0 h-full">
      <form onSubmit={submit} className="shrink-0 p-3 border-b border-border flex flex-col gap-2 tablet:flex-row tablet:items-center">
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={record ? `Add a task for ${record.title}` : "Add a task"} className="flex-1" disabled={!record} />
        <div className="flex items-center gap-2">
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-[140px]" aria-label="Due date" />
          <Select value={priority} onChange={(e) => setPriority(e.target.value as Task["priority"])} className="w-[100px]" aria-label="Priority"><option>High</option><option>Medium</option><option>Low</option></Select>
          <Button type="submit" variant="primary" disabled={!text.trim() || creating || !record}>{creating ? <Loader2 className="animate-spin" /> : <Plus />}Add</Button>
        </div>
      </form>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {isLoading ? <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
        : tasks.length === 0 ? <EmptyState icon={<ListTodo />} title="No tasks yet" hint="Tasks are Frappe ToDos linked to this record, so assignment and reminders work from Desk too." compact />
        : (
          <ul className="divide-y divide-border">
            {open.map((t) => <TaskRow key={t.name} t={t} onToggle={() => setStatus(t.name, "Closed")} />)}
            {done.length > 0 && (
              <li className="px-3 py-2"><button type="button" className="text-xs text-ink-3 hover:text-ink-1" onClick={() => setShowDone((v) => !v)}>{showDone ? "Hide" : "Show"} {done.length} completed</button></li>
            )}
            {showDone && done.map((t) => <TaskRow key={t.name} t={t} onToggle={() => setStatus(t.name, "Open")} />)}
          </ul>
        )}
      </div>
    </div>
  );
}

function TaskRow({ t, onToggle }: { t: Task; onToggle: () => void }) {
  const closed = t.status !== "Open";
  const overdue = !closed && t.date && new Date(t.date) < new Date(new Date().toDateString());
  return (
    <li className="flex items-start gap-2 px-3 py-2 min-w-0 t2-host">
      <button type="button" aria-label={closed ? "Reopen" : "Complete"} onClick={onToggle} className="mt-0.5 text-ink-3 hover:text-crayon-green-text shrink-0">{closed ? <CheckCircle2 className="size-4 text-crayon-green-base" /> : <Circle className="size-4" />}</button>
      <div className="flex-1 min-w-0">
        <p className={cn("text-sm text-ink-1 break-words", closed && "line-through text-ink-3")}>{stripHtml(t.description)}</p>
        <div className="flex items-center gap-1.5 mt-0.5 text-xs text-ink-3 min-w-0 flex-wrap">
          {t.date && <span className={cn("tabular-nums", overdue && "text-crayon-rose-text font-medium")}>{overdue ? "Overdue · " : "Due "}{t.date}</span>}
          {t.priority === "High" && <Chip size="sm" accent="rose" label="High" />}
          {t.allocated_to && <span className="truncate">→ {t.allocated_to}</span>}
          <a href={deskUrl("ToDo", t.name)} target="_blank" rel="noreferrer" className="t2-reveal inline-flex items-center gap-0.5 hover:text-ink-1"><ExternalLink className="size-3" />Desk</a>
        </div>
      </div>
    </li>
  );
}
