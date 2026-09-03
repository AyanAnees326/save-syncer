import { createContext, useContext } from "react";

export type ThemeMode = "light" | "dark" | "system";
export type AccentId = "indigo" | "violet" | "sky" | "emerald" | "rose" | "amber";

export interface Accent {
  id: AccentId;
  label: string;
  /** A representative solid color for the swatch button - matches --accent in dark
   * mode, since that is what most of the app's chrome runs in by default. */
  swatch: string;
}

export const ACCENTS: Accent[] = [
  { id: "indigo", label: "Indigo", swatch: "#6366f1" },
  { id: "violet", label: "Violet", swatch: "#a78bfa" },
  { id: "sky", label: "Sky", swatch: "#38bdf8" },
  { id: "emerald", label: "Emerald", swatch: "#34d399" },
  { id: "rose", label: "Rose", swatch: "#fb7185" },
  { id: "amber", label: "Amber", swatch: "#fbbf24" },
];

export const STORAGE_KEY = "savesync-theme";

export interface ThemePreference {
  mode: ThemeMode;
  accent: AccentId;
}

export const DEFAULT_THEME: ThemePreference = { mode: "dark", accent: "indigo" };

export interface ThemeContextValue extends ThemePreference {
  resolvedDark: boolean;
  setMode: (mode: ThemeMode) => void;
  setAccent: (accent: AccentId) => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
