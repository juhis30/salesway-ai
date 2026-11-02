import type { Config } from "tailwindcss";

// Colours are CSS variables (src/app/globals.css) so light and dark mode swap in one place.
// color-mix lets opacity modifiers like `bg-critical/5` work on variable colours.
const token = (name: string) => `color-mix(in srgb, var(--${name}) calc(<alpha-value> * 100%), transparent)`;

const NAMES = [
  "page", "surface", "surface-2", "ink", "ink-2", "muted", "grid", "accent", "accent-ink", "accent-soft",
  "good", "warning", "serious", "critical", "up", "down",
];

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ...Object.fromEntries(NAMES.map((n) => [n, token(n)])),
        line: "var(--border)",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
