import { useEffect, useState } from "react";

export type Breakpoint = "phone" | "tablet" | "laptop" | "wide";

/** UX-001 §3.1: phone <640, tablet 640–1023, laptop 1024–1439, wide ≥1440. */
export function bpFor(width: number): Breakpoint {
  if (width < 640) return "phone";
  if (width < 1024) return "tablet";
  if (width < 1440) return "laptop";
  return "wide";
}

export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>(() => bpFor(window.innerWidth));
  useEffect(() => {
    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setBp(bpFor(window.innerWidth)));
    };
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); cancelAnimationFrame(raf); };
  }, []);
  return bp;
}

export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() => window.matchMedia?.("(pointer: coarse)").matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.("(pointer: coarse)");
    if (!mq) return;
    const h = (e: MediaQueryListEvent) => setCoarse(e.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);
  return coarse;
}
