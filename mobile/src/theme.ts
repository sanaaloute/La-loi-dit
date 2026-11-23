import { Platform } from "react-native";

// Brand palette ported from the web app (frontend/app/globals.css):
// warm cream neutrals, ONE deep institutional navy accent, bronze gold as the
// support hue (badges, landmarks, key highlights). Yellow stays confined to
// warnings, muted red to errors/destructive actions.
export const lightColors = {
  accent: "#15375A",
  accentHover: "#0F2945",
  accentLight: "#E7EDF5",
  gold: "#9A7A1E",
  goldHover: "#7A6116",
  goldLight: "#F7F0DC",
  ink: "#1A202E",
  inkSoft: "#524C42",
  surface: "#F6F2EA",
  surfaceElevated: "#FFFFFF",
  border: "#E4DCCD",
  warnBg: "#FEF9C3",
  warnBorder: "#FACC15",
  warnText: "#854D0E",
  danger: "#B91C1C",
  dangerBg: "#FEE2E2",
  dangerBorder: "#FCA5A5",
  muted: "#736B5E",
  faint: "#A09686",
  // Fixed deep-navy surface (web sidebar → mobile tab bar) + its text hue.
  sidebarBg: "#121C30",
  sidebarText: "#A3AFC7",
  // Fixed pure white for text/icons on accent or always-dark surfaces.
  paper: "#FFFFFF",
};

export type ThemeColors = typeof lightColors;

// Dark palette ported from the web app's .dark block: the accent role passes
// to bronze gold, gold itself lightens. The danger colors have no dark
// variant on the web: dark-appropriate values.
export const darkColors: ThemeColors = {
  accent: "#9A7A1E",
  accentHover: "#7A6116",
  accentLight: "#382F1C",
  gold: "#D3AC5B",
  goldHover: "#E4C480",
  goldLight: "#382F1C",
  ink: "#F0ECE4",
  inkSoft: "#BEB5A6",
  surface: "#0F0E0C",
  surfaceElevated: "#1A1916",
  border: "#302D28",
  warnBg: "#42320A",
  warnBorder: "#A16207",
  warnText: "#FDE68A",
  danger: "#F87171",
  dangerBg: "#451A1A",
  dangerBorder: "#7F1D1D",
  muted: "#A09686",
  faint: "#736B5E",
  sidebarBg: "#0E1525",
  sidebarText: "#94A0B6",
  paper: "#FFFFFF",
};

// Web headings use Tailwind's serif stack (Georgia family). RN has no generic
// "ui-serif" alias, so pick the platform serif face (iOS Georgia, Android serif).
export const serifFont: string = Platform.select({
  ios: "Georgia",
  android: "serif",
  default: "serif",
});
