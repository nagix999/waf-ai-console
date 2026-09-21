import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { messages, translate } from "./consoleMessages.js";

export const themeOptions = ["sk", "light", "dark"];
export const localeOptions = ["ko", "en"];
export function readPreference(storage, key, options, fallback) {
  try { const value = storage?.getItem(key); return options.includes(value) ? value : fallback; }
  catch { return fallback; }
}
const defaults = { locale: "ko", theme: "sk", setLocale: () => {}, setTheme: () => {}, t: (key, values) => translate("ko", key, values) };
const Preferences = createContext(defaults);

export function ConsolePreferencesProvider({ children }) {
  const storage = () => { try { return window.localStorage; } catch { return null; } };
  const [locale, setLocale] = useState(() => readPreference(storage(), "waf-console-locale", localeOptions, "ko"));
  const [theme, setTheme] = useState(() => readPreference(storage(), "waf-console-theme", themeOptions, "sk"));
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dataset.theme = theme;
    try { const local = storage(); local?.setItem("waf-console-locale", locale); local?.setItem("waf-console-theme", theme); } catch { /* Optional preferences, never content. */ }
  }, [locale, theme]);
  const value = useMemo(() => ({ locale, theme,
    setLocale: next => { if (localeOptions.includes(next)) setLocale(next); },
    setTheme: next => { if (themeOptions.includes(next)) setTheme(next); },
    t: (key, values) => translate(locale, key, values),
  }), [locale, theme]);
  return <Preferences.Provider value={value}>{children}</Preferences.Provider>;
}
export const useConsolePreferences = () => useContext(Preferences);
export { messages };
