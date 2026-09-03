import { cn } from "../ui/utils";

const SIZE: Record<number, string> = {
  20: "size-5 text-2xs",
  24: "size-6 text-2xs",
  28: "size-7 text-xs",
  32: "size-8 text-xs",
  40: "size-10 text-sm",
  56: "size-14 text-md",
};

const TONES = ["blue", "green", "amber", "violet", "teal", "plum", "sand", "rose"] as const;

function toneFor(name: string) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return TONES[h % TONES.length];
}

export function initials(name: string): string {
  return (name || "?")
    .split(/\s+/)
    .map((w) => w[0])
    .filter(Boolean)
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/** Flat tinted avatar. Colour is derived from the name so it is stable across the app. */
export function Avatar({ name, src, size = 32, className }: { name: string; src?: string; size?: 20 | 24 | 28 | 32 | 40 | 56; className?: string }) {
  if (src) {
    return <img src={src} alt={name} className={cn("rounded-full object-cover shrink-0", SIZE[size], className)} />;
  }
  const t = toneFor(name || "");
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex items-center justify-center rounded-full font-semibold shrink-0 select-none",
        SIZE[size],
        `bg-crayon-${t}-tint text-crayon-${t}-text`,
        className
      )}
    >
      {initials(name)}
    </span>
  );
}
