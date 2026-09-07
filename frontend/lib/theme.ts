"use client";

// Theme handling: "light" | "dark" | "auto" (follows the OS preference).
// The choice is persisted in localStorage and applied by toggling the `dark`
// class on <html>; app/layout.tsx applies it before first paint to avoid a
// flash of the wrong theme.

export type ThemeMode = "light" | "dark" | "auto";

const STORAGE_KEY = "yawoto-theme";

export function getThemeMode(): ThemeMode {
  if (typeof window === "undefined") return "auto";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" || stored === "auto" ? stored : "auto";
}

export function isDarkMode(mode: ThemeMode): boolean {
  if (mode === "dark") return true;
  if (mode === "auto" && typeof window !== "undefined") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  return false;
}

export function applyThemeMode(mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", isDarkMode(mode));
}

export function setThemeMode(mode: ThemeMode): void {
  window.localStorage.setItem(STORAGE_KEY, mode);
  applyThemeMode(mode);
}
