#!/usr/bin/env python3
"""
W10 — mechanical token re-skin for shared (non-frozen) components.
Maps zinc/gradient/700-colour classes to UX-001 tokens and raises sub-12px text.
Idempotent. Frozen legacy files are never touched (see FROZEN).
"""
import re, sys, pathlib

FROZEN = {"ChannelTabsView.tsx", "LeftSidebar.tsx", "ChatThreadList.tsx", "LegacyApp.tsx"}

ACCENT = {
    "blue": "blue", "sky": "teal", "cyan": "teal", "teal": "teal", "indigo": "violet", "purple": "violet", "violet": "violet",
    "green": "green", "emerald": "green", "red": "rose", "rose": "rose", "amber": "amber", "orange": "amber", "yellow": "amber", "pink": "plum",
}
acc = "|".join(ACCENT)

RULES = [
    # gradient buttons → flat primary
    (r"bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700(?: text-white)?", "bg-crayon-blue-base hover:bg-crayon-blue-text text-white"),
    (r"hover:from-blue-600 hover:to-purple-700", ""),
    (r"bg-gradient-to-(?:r|br|b|l|bl|t|tr|tl) from-[a-z]+-\d+/\d+ (?:via-[a-z]+-\d+/\d+ )?to-[a-z]+-\d+/\d+", "bg-surface-sunken"),
    (r"bg-gradient-to-(?:r|br|b|l|bl|t|tr|tl) from-zinc-\d+ (?:via-zinc-\d+ )?to-(?:zinc-\d+|white)", "bg-surface-sunken"),
    (r"bg-gradient-to-(?:r|br|b) from-[a-z]+-\d+ to-[a-z]+-\d+", "bg-crayon-blue-tint text-crayon-blue-text"),
    # sub-12px → 12px (numeric badges keep 2xs by hand)
    (r"text-\[(?:8|9|10|11)px\]", "text-xs"),
    (r"\btext-2xl\b", "text-lg"), (r"\btext-xl\b", "text-lg"),
    (r"\bfont-bold\b", "font-semibold"), (r"\bfont-light\b", "font-normal"),
    (r"\buppercase tracking-wider?\b ?", ""), (r"\btracking-wider? uppercase\b ?", ""),
    # zinc neutrals
    (r"\btext-zinc-900\b", "text-ink-1"), (r"\btext-zinc-800\b", "text-ink-1"), (r"\btext-zinc-700\b", "text-ink-2"),
    (r"\btext-zinc-600\b", "text-ink-3"), (r"\btext-zinc-500\b", "text-ink-3"), (r"\btext-zinc-400\b", "text-ink-muted"),
    (r"\bhover:text-zinc-900\b", "hover:text-ink-1"), (r"\bhover:text-zinc-700\b", "hover:text-ink-2"), (r"\bhover:text-zinc-600\b", "hover:text-ink-2"),
    (r"\bgroup-hover:text-zinc-900\b", "group-hover:text-ink-1"),
    (r"\bplaceholder:text-zinc-\d+\b", "placeholder:text-ink-3"), (r"\bplaceholder-zinc-\d+\b", "placeholder:text-ink-3"),
    (r"\bbg-zinc-50(?:/\d+)?\b", "bg-surface"), (r"\bbg-zinc-100(?:/\d+)?\b", "bg-surface-sunken"),
    (r"\bbg-zinc-200(?:/\d+)?\b", "bg-surface-active"), (r"\bbg-zinc-300(?:/\d+)?\b", "bg-surface-active"),
    (r"\bhover:bg-zinc-50(?:/\d+)?\b", "hover:bg-surface-hover"), (r"\bhover:bg-zinc-100(?:/\d+)?\b", "hover:bg-surface-hover"), (r"\bhover:bg-zinc-200(?:/\d+)?\b", "hover:bg-surface-active"),
    (r"\bbg-white(?:/\d+)?\b", "bg-surface"), (r"\bhover:bg-white/\d+\b", "hover:bg-surface-hover"),
    (r"\bborder-zinc-200(?:/\d+)?\b", "border-border"), (r"\bborder-zinc-300(?:/\d+)?\b", "border-border-strong"),
    (r"\bhover:border-zinc-300\b", "hover:border-border-strong"), (r"\bfocus:border-zinc-300\b", "focus:border-crayon-blue-base"),
    (r"\bdivide-zinc-200(?:/\d+)?\b", "divide-border"), (r"\bdivide-zinc-300(?:/\d+)?\b", "divide-border"),
    (r"\bring-zinc-300\b", "ring-border-strong"), (r"\bring-zinc-300/50\b", "ring-border-strong"),
    (r"\bfocus-visible:ring-zinc-300/50\b", "focus-visible:ring-crayon-blue-base"),
    # dark-theme leftovers
    (r"\btext-amber-100(?:/\d+)?\b", "text-ink-1"), (r"\btext-purple-200\b", "text-crayon-violet-text"),
    (r"\bbg-black/\d+\b", "bg-ink-1/40"), (r"\bshadow-black/\d+\b ?", ""), (r"\bshadow-blue-500/\d+\b ?", ""), (r"\bshadow-zinc-900/\d+\b ?", ""),
    (r"\bbackdrop-blur(?:-\w+)?\b ?", ""),
    (r"\bshadow-(?:2xl|xl|lg|md|sm)\b", "shadow-ex"), (r"\bhover:shadow-(?:2xl|xl|lg|md|sm)\b ?", ""),
    (r"\brounded-2xl\b", "rounded-xl"),
    # solid accent buttons
    (rf"\bbg-({acc})-(?:500|600) hover:bg-(?:{acc})-(?:600|700)(?: text-white)?", lambda m: f"bg-crayon-{ACCENT[m.group(1)]}-base hover:bg-crayon-{ACCENT[m.group(1)]}-text text-white"),
    (rf"\bhover:bg-({acc})-(?:600|700)\b", lambda m: f"hover:bg-crayon-{ACCENT[m.group(1)]}-text"),
    (rf"\bbg-({acc})-(?:500|600) text-white", lambda m: f"bg-crayon-{ACCENT[m.group(1)]}-base text-white"),
    (rf"\bbg-({acc})-(?:500|600)\b(?!/)", lambda m: f"bg-crayon-{ACCENT[m.group(1)]}-base"),
    # tints / text / borders
    (rf"\bbg-({acc})-\d+/\d+\b", lambda m: f"bg-crayon-{ACCENT[m.group(1)]}-tint"),
    (rf"\bhover:bg-({acc})-\d+/\d+\b", lambda m: f"hover:bg-crayon-{ACCENT[m.group(1)]}-tint"),
    (rf"\bbg-({acc})-50\b", lambda m: f"bg-crayon-{ACCENT[m.group(1)]}-tint"),
    (rf"\btext-({acc})-(?:600|700|800|900)\b", lambda m: f"text-crayon-{ACCENT[m.group(1)]}-text"),
    (rf"\bhover:text-({acc})-(?:600|700|800)\b", lambda m: f"hover:text-crayon-{ACCENT[m.group(1)]}-text"),
    (rf"\bgroup-hover:text-({acc})-(?:600|700)\b", lambda m: f"group-hover:text-crayon-{ACCENT[m.group(1)]}-text"),
    (rf"\btext-({acc})-(?:400|500)\b", lambda m: f"text-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bfill-({acc})-\d+\b", lambda m: f"fill-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bborder-({acc})-\d+(?:/\d+)?\b", lambda m: f"border-crayon-{ACCENT[m.group(1)]}-base/40"),
    (rf"\bhover:border-({acc})-\d+(?:/\d+)?\b", lambda m: f"hover:border-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bfocus:border-({acc})-\d+(?:/\d+)?\b", lambda m: f"focus:border-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bfocus-visible:border-({acc})-\d+\b", lambda m: f"focus-visible:border-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bfocus-visible:ring-({acc})-\d+\b", lambda m: f"focus-visible:ring-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bfocus-within:border-({acc})-\d+\b", lambda m: f"focus-within:border-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bring-({acc})-\d+\b", lambda m: f"ring-crayon-{ACCENT[m.group(1)]}-base"),
    (rf"\bdivide-({acc})-\d+(?:/\d+)?\b", lambda m: "divide-border"),
    # collapse double spaces inside class strings
    (r'className="([^"]*)"', lambda m: 'className="' + re.sub(r"\s{2,}", " ", m.group(1)).strip() + '"'),
]

def reskin(text: str) -> str:
    for pat, rep in RULES:
        text = re.sub(pat, rep, text)
    return text

def main(paths):
    changed = 0
    for p in paths:
        p = pathlib.Path(p)
        if p.name in FROZEN or "/mobile/" in str(p): continue
        src = p.read_text()
        out = reskin(src)
        if out != src:
            p.write_text(out); changed += 1
            print("reskinned", p)
    print(f"{changed} files changed")

if __name__ == "__main__":
    main(sys.argv[1:])
