import { useEffect, useRef, useState } from "react";
import { Bold, Italic, Underline, List, ListOrdered, Link2, Code2, Eraser } from "lucide-react";
import { cn } from "../ui/utils";

/**
 * Small rich-text editor for email bodies: formatting toolbar + an HTML source toggle.
 * Value is always HTML. No external dependency; execCommand is enough for bold/italic/lists/links.
 */
export function EmailEditor({ value, onChange, placeholder, className }: { value: string; onChange: (html: string) => void; placeholder?: string; className?: string }) {
  const [source, setSource] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!source && ref.current && ref.current.innerHTML !== value) ref.current.innerHTML = value || ""; }, [value, source]);
  const cmd = (c: string, arg?: string) => { ref.current?.focus(); document.execCommand(c, false, arg); onChange(ref.current?.innerHTML || ""); };
  const btn = (icon: React.ReactNode, title: string, fn: () => void) => <button type="button" title={title} onMouseDown={(e) => { e.preventDefault(); fn(); }} className="size-7 rounded text-ink-3 hover:text-ink-1 hover:bg-surface-hover flex items-center justify-center [&>svg]:size-4">{icon}</button>;
  return (
    <div className={cn("min-w-0", className)}>
      <div className="flex items-center gap-0.5 px-1 h-8 border-b border-border">
        {btn(<Bold />, "Bold (Ctrl+B)", () => cmd("bold"))}
        {btn(<Italic />, "Italic (Ctrl+I)", () => cmd("italic"))}
        {btn(<Underline />, "Underline (Ctrl+U)", () => cmd("underline"))}
        {btn(<List />, "Bullet list", () => cmd("insertUnorderedList"))}
        {btn(<ListOrdered />, "Numbered list", () => cmd("insertOrderedList"))}
        {btn(<Link2 />, "Link", () => { const u = window.prompt("Link URL"); if (u) cmd("createLink", u); })}
        {btn(<Eraser />, "Clear formatting", () => cmd("removeFormat"))}
        <span className="flex-1" />
        <button type="button" onClick={() => setSource((s) => !s)} className={cn("h-6 px-2 rounded text-xs flex items-center gap-1", source ? "bg-surface-active text-ink-1" : "text-ink-3 hover:text-ink-1")} title="Edit the raw HTML"><Code2 className="size-3.5" />{source ? "Rich text" : "HTML"}</button>
      </div>
      {source ? (
        <textarea value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} className="w-full min-h-[140px] max-h-[40vh] p-2 text-xs font-mono bg-surface-sunken outline-none resize-y" placeholder="<p>HTML…</p>" />
      ) : (
        <div ref={ref} contentEditable suppressContentEditableWarning onInput={() => onChange(ref.current?.innerHTML || "")} data-placeholder={placeholder}
          className="min-h-[140px] max-h-[40vh] overflow-y-auto p-2 text-sm text-ink-1 outline-none [&_a]:underline [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 empty:before:content-[attr(data-placeholder)] empty:before:text-ink-3" />
      )}
    </div>
  );
}
