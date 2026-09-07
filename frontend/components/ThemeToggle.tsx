"use client";

import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { applyThemeMode, getThemeMode, setThemeMode, type ThemeMode } from "@/lib/theme";

const MODES: { id: ThemeMode; label: string; icon: React.ElementType }[] = [
  { id: "light", label: "Thème clair", icon: Sun },
  { id: "dark", label: "Thème sombre", icon: Moon },
  { id: "auto", label: "Thème automatique", icon: Monitor },
];

/** Cycles light → dark → auto; in "auto" the OS preference is followed live. */
export default function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>("auto");

  useEffect(() => {
    setMode(getThemeMode());
  }, []);

  // Keep the dark class in sync with the OS when the mode is "auto".
  useEffect(() => {
    if (mode !== "auto") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyThemeMode("auto");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [mode]);

  function cycle() {
    const next = MODES[(MODES.findIndex((m) => m.id === mode) + 1) % MODES.length].id;
    setMode(next);
    setThemeMode(next);
  }

  const current = MODES.find((m) => m.id === mode) ?? MODES[2];
  const Icon = current.icon;

  return (
    <button
      type="button"
      onClick={cycle}
      className="flex h-10 w-10 items-center justify-center rounded-lg border border-gray-300 bg-gray-50 text-gray-600 transition-colors hover:bg-gray-100"
      title={`${current.label} — cliquer pour changer`}
      aria-label={`${current.label} — cliquer pour changer`}
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}
