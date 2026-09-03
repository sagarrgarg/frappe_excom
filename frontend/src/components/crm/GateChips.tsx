import { ShieldCheck, ShieldAlert } from "lucide-react";
import { Chip } from "../primitives";

const LABELS: Record<string, string> = { territory: "Territory", onboarding: "Onboarding", compliance: "Compliance", payment: "Payment", feasibility: "Feasibility", sampling: "Sampling", date_feasible: "Date feasible" };

/** Gate chips (HLD-003 §6): green = cleared, amber = open. Click a blocked gate to override (managers). */
export function GateChips({ flags, onOverride, size = "sm" }: { flags: Record<string, number>; onOverride?: (gate: string) => void; size?: "sm" | "md" }) {
  const entries = Object.entries(flags).filter(([k]) => !k.startsWith("_"));
  if (!entries.length) return null;
  return (
    <>
      {entries.map(([k, v]) => (
        <Chip key={k} size={size} accent={v ? "green" : "amber"} icon={v ? <ShieldCheck /> : <ShieldAlert />} label={LABELS[k] || k} title={v ? "Gate cleared" : "Gate not cleared — click to override (managers)"} onClick={!v && onOverride ? () => onOverride(k) : undefined} />
      ))}
    </>
  );
}
