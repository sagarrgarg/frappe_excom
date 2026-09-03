import { useState } from "react";
import { Loader2, StickyNote, Send } from "lucide-react";
import { useNotes } from "../../hooks/useNotes";
import type { RecordRef } from "../../hooks/useRecordLinks";
import { Button, Textarea, EmptyState, Avatar } from "../primitives";
import { formatServerShortDateTime, parseFrappeDateTime } from "../../utils/datetime";

/** Notes tab — core Comment on the linked party (a note about the party; thread notes stay in the feed). */
export function NotesTab({ record, identityId }: { record: RecordRef | null; identityId: string }) {
  const { notes, isLoading, error, adding, addNote } = useNotes(identityId, record);
  const [text, setText] = useState("");
  const submit = async () => { if (!text.trim()) return; await addNote(text.trim()); setText(""); };
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="shrink-0 p-3 border-b border-border">
        <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={record ? `Note about ${record.title} — lands on the ${record.doctype} in Desk and in the chat` : "Note about this contact — lands on the conversation and in the chat"} rows={2}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit(); }} />
        <div className="flex justify-end mt-2"><Button variant="primary" size="sm" onClick={submit} disabled={!text.trim() || adding}>{adding ? <Loader2 className="animate-spin" /> : <Send />}Add note</Button></div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {isLoading ? <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
        : error ? <EmptyState icon={<StickyNote />} title="Can't read notes" hint="You need read access to the linked record to see its comments." compact />
        : notes.length === 0 ? <EmptyState icon={<StickyNote />} title="No notes yet" hint="A note typed here or as an internal note in the chat is the same thing: a Comment on the linked record, visible in Desk." compact />
        : (
          <ul className="divide-y divide-border">
            {notes.map((n) => (
              <li key={n.name} className="flex gap-2 px-3 py-2.5 min-w-0">
                <Avatar name={n.comment_by || n.owner} size={24} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 text-xs text-ink-3 min-w-0"><span className="truncate text-ink-2 font-medium">{n.comment_by || n.owner}</span><span className="truncate text-ink-muted">on {n.on_doctype.replace("Excom ", "")}{n.on_doctype !== "Excom Thread" && n.on_doctype !== "Omni Identity" ? ` ${n.on_name}` : ""}</span><span className="ml-auto tabular-nums shrink-0">{formatServerShortDateTime(parseFrappeDateTime(n.creation))}</span></div>
                  <div className="text-base text-ink-1 break-words mt-0.5 [&_a]:underline" dangerouslySetInnerHTML={{ __html: n.content }} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
