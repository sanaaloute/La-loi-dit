// Theme context: light/dark mode. The user's choice is persisted in
// SecureStore under "yawoto-theme" (mirrors the web app's localStorage key);
// it is not sensitive, just kept alongside the other client state.
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useColorScheme } from "react-native";
import * as SecureStore from "expo-secure-store";
import { darkColors, lightColors, type ThemeColors } from "./theme";

export type ThemeMode = "light" | "dark";

const THEME_KEY = "yawoto-theme";

interface ThemeContextValue {
  colors: ThemeColors;
  isDark: boolean;
  mode: ThemeMode;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const systemScheme = useColorScheme();
  const systemMode: ThemeMode = systemScheme === "dark" ? "dark" : "light";
  // null = no stored choice (or not loaded yet): follow the system scheme.
  // Rendering is never blocked on the SecureStore read.
  const [choice, setChoice] = useState<ThemeMode | null>(null);

  useEffect(() => {
    SecureStore.getItemAsync(THEME_KEY)
      .then((value) => {
        if (value === "light" || value === "dark") setChoice(value);
      })
      .catch(() => {
        // Unreadable store: keep following the system scheme.
      });
  }, []);

  const mode = choice ?? systemMode;

  const toggleTheme = useCallback(() => {
    const next: ThemeMode = mode === "dark" ? "light" : "dark";
    setChoice(next);
    // Fire-and-forget: the in-memory state already reflects the choice.
    SecureStore.setItemAsync(THEME_KEY, next).catch(() => {});
  }, [mode]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      colors: mode === "dark" ? darkColors : lightColors,
      isDark: mode === "dark",
      mode,
      toggleTheme,
    }),
    [mode, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider");
  return ctx;
}
