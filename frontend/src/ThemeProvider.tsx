import { useEffect, useState, type ReactNode } from "react";
import {
  DEFAULT_THEME,
  STORAGE_KEY,
  ThemeContext,
  type AccentId,
  type ThemeMode,
  type ThemePreference,
} from "./theme";

function readStored(): ThemePreference {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_THEME;
    const parsed = JSON.parse(raw);
    return {
      mode: parsed.mode === "light" || parsed.mode === "dark" || parsed.mode === "system"
        ? parsed.mode
        : DEFAULT_THEME.mode,
      accent: typeof parsed.accent === "string" ? (parsed.accent as AccentId) : DEFAULT_THEME.accent,
    };
  } catch {
    return DEFAULT_THEME;
  }
}

/** Applies the theme to <html> - the index.html inline script does this once before
 * paint; this keeps it in sync as the user changes settings or the OS theme flips. */
function applyToDocument(pref: ThemePreference, systemDark: boolean) {
  const root = document.documentElement;
  const dark = pref.mode === "dark" || (pref.mode === "system" && systemDark);
  root.classList.toggle("dark", dark);
  root.setAttribute("data-accent", pref.accent);
  return dark;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPref] = useState<ThemePreference>(readStored);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false,
  );
  // Mirrors applyToDocument's result so consumers (e.g. an OS-style toggle icon) can
  // read the resolved theme without re-deriving it - kept in an effect alongside the
  // DOM mutation itself, since both need to happen after commit, not during render.
  const [resolvedDark, setResolvedDark] = useState(() => applyToDocument(pref, systemDark));

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  // DOM mutation belongs in an effect, not render (a bare useMemo here previously
  // left dependent styles - e.g. background-color: var(--accent) on buttons - out of
  // sync with the custom property itself, since render-phase DOM writes fall outside
  // React's normal commit/paint ordering).
  useEffect(() => {
    setResolvedDark(applyToDocument(pref, systemDark));
  }, [pref, systemDark]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(pref));
    } catch {
      /* private browsing / storage disabled - theme just won't persist */
    }
  }, [pref]);

  const setMode = (mode: ThemeMode) => setPref((p) => ({ ...p, mode }));
  const setAccent = (accent: AccentId) => setPref((p) => ({ ...p, accent }));

  return (
    <ThemeContext.Provider value={{ ...pref, resolvedDark, setMode, setAccent }}>
      {children}
    </ThemeContext.Provider>
  );
}
