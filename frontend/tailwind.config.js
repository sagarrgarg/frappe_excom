/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Legacy alias kept for frozen components; new tree uses tokens below.
        primary: {
          50: "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#3b82f6",
          600: "#2563eb",
          700: "#1d4ed8",
          800: "#1e40af",
          900: "#1e3a8a",
        },
        // UX-001 §2.2 tokens → bg-surface-sunken, text-ink-2, bg-crayon-green-tint
        surface: {
          DEFAULT: "var(--ex-surface)",
          sunken: "var(--ex-surface-sunken)",
          hover: "var(--ex-surface-hover)",
          active: "var(--ex-surface-active)",
        },
        border: {
          DEFAULT: "var(--ex-border)",
          strong: "var(--ex-border-strong)",
        },
        ink: {
          1: "var(--ex-ink-1)",
          2: "var(--ex-ink-2)",
          3: "var(--ex-ink-3)",
          muted: "var(--ex-ink-muted)",
        },
        crayon: {
          blue: { tint: "var(--ex-blue-tint)", base: "var(--ex-blue-base)", text: "var(--ex-blue-text)" },
          green: { tint: "var(--ex-green-tint)", base: "var(--ex-green-base)", text: "var(--ex-green-text)" },
          amber: { tint: "var(--ex-amber-tint)", base: "var(--ex-amber-base)", text: "var(--ex-amber-text)" },
          rose: { tint: "var(--ex-rose-tint)", base: "var(--ex-rose-base)", text: "var(--ex-rose-text)" },
          violet: { tint: "var(--ex-violet-tint)", base: "var(--ex-violet-base)", text: "var(--ex-violet-text)" },
          teal: { tint: "var(--ex-teal-tint)", base: "var(--ex-teal-base)", text: "var(--ex-teal-text)" },
          plum: { tint: "var(--ex-plum-tint)", base: "var(--ex-plum-base)", text: "var(--ex-plum-text)" },
          sand: { tint: "var(--ex-sand-tint)", base: "var(--ex-sand-base)", text: "var(--ex-sand-text)" },
        },
      },
      // UX-001 §2.3 type scale. 2xs is numeric badges only.
      fontSize: {
        "2xs": ["11px", { lineHeight: "14px", fontWeight: "600" }],
        xs: ["12px", { lineHeight: "16px" }],
        sm: ["13px", { lineHeight: "18px" }],
        base: ["14px", { lineHeight: "20px" }],
        md: ["15px", { lineHeight: "20px", fontWeight: "600" }],
        lg: ["17px", { lineHeight: "24px", fontWeight: "600" }],
      },
      boxShadow: {
        ex: "var(--ex-shadow)",
        none: "none",
      },
      spacing: {
        rail: "var(--ex-rail-w)",
        list: "var(--ex-list-w)",
        details: "var(--ex-details-w)",
        "row-list": "var(--ex-row-list)",
        "row-dense": "var(--ex-row-dense)",
        hit: "var(--ex-hit-min)",
        "header-h": "var(--ex-header-h)",
        "context-h": "var(--ex-context-h)",
        "tabs-h": "var(--ex-tabs-h)",
      },
      screens: {
        phone: { max: "639px" },
        tablet: "640px",
        laptop: "1024px",
        wide: "1440px",
      },
      transitionDuration: { DEFAULT: "120ms" },
    },
  },
  plugins: [],
};
