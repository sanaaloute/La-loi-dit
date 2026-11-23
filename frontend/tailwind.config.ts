import type { Config } from "tailwindcss";

// Theme palettes are driven by CSS variables (see app/globals.css) so the
// whole UI can switch between light and dark themes by toggling the `dark`
// class on <html>. Every var holds an "R G B" triplet; `<alpha-value>` keeps
// Tailwind opacity modifiers (e.g. bg-accent/40) working.
function themed(name: string) {
  return `rgb(var(${name}) / <alpha-value>)`;
}

// Restrained palette (investor-approved direction): warm cream surfaces,
// near-black ink, ONE deep-navy accent, bronze gold as the support hue,
// yellow confined to warnings, muted red (Tailwind red-700 = #b91c1c)
// confined to errors/destructive actions. No gradients on surfaces. The dark
// theme remaps the same variables in globals.css; the sidebar keeps its own
// fixed deep-navy palette in both themes.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        white: themed("--c-white"),
        // Fixed pure white for text/blocks on always-dark surfaces (sidebar,
        // hero, veille card) — unlike `white`, it never remaps in dark mode.
        paper: "#ffffff",
        gray: {
          50: themed("--c-gray-50"),
          100: themed("--c-gray-100"),
          200: themed("--c-gray-200"),
          300: themed("--c-gray-300"),
          400: themed("--c-gray-400"),
          500: themed("--c-gray-500"),
          600: themed("--c-gray-600"),
          700: themed("--c-gray-700"),
          800: themed("--c-gray-800"),
          900: themed("--c-gray-900"),
        },
        // Accent: deep institutional navy (primary actions, links, active states).
        accent: {
          DEFAULT: themed("--c-accent"),
          hover: themed("--c-accent-hover"),
          light: themed("--c-accent-light"),
        },
        // Support hue: bronze gold (premium highlights, badges, key metrics).
        gold: {
          DEFAULT: themed("--c-gold"),
          hover: themed("--c-gold-hover"),
          light: themed("--c-gold-light"),
        },
        // App sidebar: fixed deep navy in both themes.
        sidebar: {
          DEFAULT: themed("--c-sidebar-bg"),
          text: themed("--c-sidebar-text"),
        },
        // Text hues: near-black primary, gray secondary.
        ink: {
          DEFAULT: themed("--c-ink"),
          soft: themed("--c-ink-soft"),
        },
        // Warnings ONLY (quota, unverified, non-blocking alerts).
        warn: {
          bg: themed("--c-warn-bg"),
          border: themed("--c-warn-border"),
          text: themed("--c-warn-text"),
        },
        // Surfaces: white / off-white.
        surface: {
          DEFAULT: themed("--c-white"),
          solid: themed("--c-white"),
          elevated: themed("--c-white"),
        },
      },
      fontFamily: {
        sans: [
          "var(--font-geist-sans, system-ui)",
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "sans-serif",
        ],
      },
      boxShadow: {
        panel: "0 8px 32px rgba(28, 25, 23, 0.08)",
      },
      animation: {
        float: "float 6s ease-in-out infinite",
      },
      keyframes: {
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-8px)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
