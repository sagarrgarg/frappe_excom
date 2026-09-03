import { useState } from "react";
import { Tag, Plus, X } from "lucide-react";
import { useTags, useThreadTags } from "../hooks/useTags";
import { Button, Chip, Input, Badge } from "./primitives";

interface TagManagerProps {
  threadId: string;
  /** Render the editor body inline (inside a Modal) instead of a popover button. */
  inline?: boolean;
  onChanged?: () => void;
}

/** Tag editor — lives in `⋯ → Tags…` (T3) in the P1 tree; popover button in legacy. */
export function TagManager({ threadId, inline, onChanged }: TagManagerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [input, setInput] = useState("");
  const { tags: allTags } = useTags();
  const { threadTags, addTag, removeTag } = useThreadTags(threadId);
  const applied = new Set(threadTags.map((t) => t.tag));
  const available = allTags.filter((t) => !applied.has(t.name));

  const add = async (name: string) => { await addTag(name); setInput(""); onChanged?.(); };
  const remove = async (name: string) => { await removeTag(name); onChanged?.(); };

  const body = (
    <div className="space-y-3 min-w-0">
      <div>
        <p className="text-xs text-ink-3 mb-1.5">Applied</p>
        {threadTags.length === 0 ? <p className="text-xs text-ink-muted">No tags</p> : (
          <div className="flex flex-wrap gap-1.5">{threadTags.map((t) => <Chip key={t.tag} label={t.tag_name} dotColor={t.color} onRemove={() => remove(t.tag)} />)}</div>
        )}
      </div>
      {available.length > 0 && (
        <div>
          <p className="text-xs text-ink-3 mb-1.5">Available</p>
          <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">{available.map((t) => <Chip key={t.name} icon={<Plus />} label={t.tag_name} dotColor={t.color} onClick={() => add(t.name)} />)}</div>
        </div>
      )}
      <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (input.trim()) add(input.trim()); }}>
        <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="New tag…" />
        <Button type="submit" size="icon" variant="primary" disabled={!input.trim()} aria-label="Create tag"><Plus /></Button>
      </form>
    </div>
  );

  if (inline) return body;
  return (
    <div className="relative">
      <Button variant="ghost" size="sm" onClick={() => setIsOpen((v) => !v)}><Tag />Tags{threadTags.length > 0 && <Badge count={threadTags.length} />}</Button>
      {isOpen && (
        <div className="absolute right-0 top-full mt-1 z-50 w-72 bg-surface border border-border rounded-lg shadow-ex p-3">
          {body}
          <div className="flex justify-end mt-2"><Button size="sm" variant="ghost" onClick={() => setIsOpen(false)}><X />Close</Button></div>
        </div>
      )}
    </div>
  );
}
