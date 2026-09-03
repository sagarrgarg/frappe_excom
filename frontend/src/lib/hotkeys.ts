import { useEffect, useRef } from "react";

/**
 * Keyboard map (P1 E6): ⌘K, j/k, ⏎, e, a, /, ⌘⏎, ⌘., g then i/t/p.
 * Single-key shortcuts are ignored while typing in inputs. `g` opens a 1s chord window.
 */
export type HotkeyHandler = (e: KeyboardEvent) => void;

export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

export function useHotkeys(map: Record<string, HotkeyHandler>, deps: unknown[] = []) {
  const chord = useRef<string | null>(null);
  const timer = useRef<number>(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const typing = isTyping(e.target);
      const k = e.key;
      // Modifier combos work everywhere.
      if (mod && !e.altKey) {
        const name = `mod+${k.toLowerCase()}`;
        if (map[name]) { e.preventDefault(); map[name](e); return; }
      }
      if (typing || mod || e.altKey) return;
      if (chord.current === "g") {
        chord.current = null;
        window.clearTimeout(timer.current);
        const name = `g ${k.toLowerCase()}`;
        if (map[name]) { e.preventDefault(); map[name](e); }
        return;
      }
      if (k === "g") {
        chord.current = "g";
        timer.current = window.setTimeout(() => { chord.current = null; }, 1000);
        return;
      }
      const name = k.length === 1 ? k.toLowerCase() : k;
      if (map[name]) { e.preventDefault(); map[name](e); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
export const MOD = isMac ? "⌘" : "Ctrl";
