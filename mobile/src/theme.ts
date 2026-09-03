// Brand palette ported from the web app (frontend/app/globals.css).
export const lightColors = {
  accent: "#1E3A8A",
  accentHover: "#1D4ED8",
  accentLight: "#EEF2FB",
  ink: "#111827",
  inkSoft: "#4B5563",
  surface: "#F9FAFB",
  surfaceElevated: "#FFFFFF",
  border: "#E5E7EB",
  warnBg: "#FEF9C3",
  warnBorder: "#FACC15",
  warnText: "#854D0E",
  danger: "#B91C1C",
  dangerBg: "#FEE2E2",
  dangerBorder: "#FCA5A5",
  muted: "#6B7280",
  faint: "#9CA3AF",
};

export type ThemeColors = typeof lightColors;

// Dark palette ported from the web app's .dark block (frontend/app/globals.css).
// The danger colors have no dark variant on the web: dark-appropriate values.
export const darkColors: ThemeColors = {
  accent: "#5B8DEF",
  accentHover: "#7DA5F5",
  accentLight: "#1E2D4B",
  ink: "#F8FAFC",
  inkSoft: "#CBD5E1",
  surface: "#0D1420",
  surfaceElevated: "#162030",
  border: "#2C3A4F",
  warnBg: "#42320A",
  warnBorder: "#A16207",
  warnText: "#FDE68A",
  danger: "#F87171",
  dangerBg: "#451A1A",
  dangerBorder: "#7F1D1D",
  muted: "#94A3B8",
  faint: "#64748B",
};
