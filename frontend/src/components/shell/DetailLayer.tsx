import { useEffect, useState } from "react";

interface Tip { x: number; y: number; lines: string[] }

/**
 * Hold Ctrl (or ⌘) and hover: anything carrying `data-detail="Title | line | line"` shows a
 * richer explanation than a title tooltip — what a button does, the full record behind a chip,
 * exact timestamps behind a relative time. Pointer devices only; touch uses long-press menus.
 */
export function DetailLayer() {
  const [active, setActive] = useState(false);
  const [tip, setTip] = useState<Tip | null>(null);

  useEffect(() => {
    const isMod = (e: KeyboardEvent) => e.key === "Control" || e.key === "Meta";
    const show = (el: Element | null) => {
      const target = el?.closest?.("[data-detail]") as HTMLElement | null;
      if (!target) { setTip(null); return; }
      const r = target.getBoundingClientRect();
      const lines = (target.dataset.detail || "").split("|").map((s) => s.trim()).filter(Boolean);
      setTip({ x: r.left, y: r.bottom + 6, lines });
    };
    const down = (e: KeyboardEvent) => {
      if (!isMod(e) || e.repeat) return;
      setActive(true);
      document.documentElement.setAttribute("data-detail-mode", "1");
      const hovered = document.querySelectorAll("[data-detail]:hover");
      show(hovered[hovered.length - 1] || null);
    };
    const off = () => { setActive(false); setTip(null); document.documentElement.removeAttribute("data-detail-mode"); };
    const up = (e: KeyboardEvent) => { if (isMod(e)) off(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", off);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", off); };
  }, []);

  useEffect(() => {
    if (!active) return;
    const over = (e: MouseEvent) => {
      const target = (e.target as Element | null)?.closest?.("[data-detail]") as HTMLElement | null;
      if (!target) { setTip(null); return; }
      const r = target.getBoundingClientRect();
      setTip({ x: r.left, y: r.bottom + 6, lines: (target.dataset.detail || "").split("|").map((s) => s.trim()).filter(Boolean) });
    };
    document.addEventListener("mouseover", over);
    return () => document.removeEventListener("mouseover", over);
  }, [active]);

  if (!active || !tip || tip.lines.length === 0) return null;
  const maxW = 320;
  const x = Math.min(tip.x, window.innerWidth - maxW - 8);
  const flip = tip.y > window.innerHeight - 160;
  return (
    <div role="tooltip" className="fixed z-[70] rounded-lg border border-border bg-surface shadow-ex px-3 py-2 text-xs text-ink-2 pointer-events-none max-w-[320px]" style={{ left: Math.max(8, x), top: flip ? undefined : tip.y, bottom: flip ? window.innerHeight - tip.y + 30 : undefined }}>
      <div className="text-sm text-ink-1 font-medium mb-0.5">{tip.lines[0]}</div>
      {tip.lines.slice(1).map((l, i) => <div key={i} className="leading-5">{l}</div>)}
    </div>
  );
}
