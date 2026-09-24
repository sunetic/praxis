import { useCallback, useMemo, useState, type ReactNode } from "react"

import {
  SHELL_COPY,
  SHELL_LOCALE_STORAGE_KEY,
  ShellI18nContext,
  normalizeLocale,
  readStoredLocale,
  type ShellI18nContextValue,
  type ShellLocale,
} from "./shellI18nContext"

export { DEFAULT_SHELL_LOCALE, SHELL_LOCALE_STORAGE_KEY } from "./shellI18nContext"
export type { ShellCopyKey, ShellLocale, ShellTranslatorFn } from "./shellI18nContext"

type ShellI18nProviderProps = {
  children: ReactNode
  initialLocale?: ShellLocale
}

export function ShellI18nProvider({ children, initialLocale }: ShellI18nProviderProps) {
  const [locale, setLocaleState] = useState<ShellLocale>(() =>
    initialLocale ? normalizeLocale(initialLocale) : readStoredLocale()
  )

  const setLocale = useCallback((next: ShellLocale) => {
    const normalized = normalizeLocale(next)
    setLocaleState(normalized)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(SHELL_LOCALE_STORAGE_KEY, normalized)
    }
  }, [])

  const value = useMemo<ShellI18nContextValue>(() => {
    const toggleLocale = () => setLocale(locale === "zh-CN" ? "en-US" : "zh-CN")
    const t = (key: Parameters<ShellI18nContextValue["t"]>[0]) => SHELL_COPY[locale][key] ?? key
    return { locale, setLocale, toggleLocale, t }
  }, [locale, setLocale])

  return <ShellI18nContext.Provider value={value}>{children}</ShellI18nContext.Provider>
}
