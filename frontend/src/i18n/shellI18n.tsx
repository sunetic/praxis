import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react"

import { SHELL_EN_US_COPY } from "./shellCopyEn"
import { SHELL_ZH_CN_COPY, type ShellCopyKey } from "./shellCopyZh"

export type { ShellCopyKey } from "./shellCopyZh"

export type ShellLocale = "zh-CN" | "en-US"

export const SHELL_LOCALE_STORAGE_KEY = "praxis.locale"
export const DEFAULT_SHELL_LOCALE: ShellLocale = "en-US"

export type ShellTranslatorFn = (key: ShellCopyKey) => string

const SHELL_COPY: Record<ShellLocale, Record<ShellCopyKey, string>> = {
  "zh-CN": SHELL_ZH_CN_COPY,
  "en-US": SHELL_EN_US_COPY,
}

function normalizeLocale(value: unknown): ShellLocale {
  if (typeof value === "string" && value in SHELL_COPY) return value as ShellLocale
  return DEFAULT_SHELL_LOCALE
}

function readStoredLocale(): ShellLocale {
  if (typeof window === "undefined") return DEFAULT_SHELL_LOCALE
  return normalizeLocale(window.localStorage.getItem(SHELL_LOCALE_STORAGE_KEY))
}

type ShellI18nContextValue = {
  locale: ShellLocale
  setLocale: (next: ShellLocale) => void
  toggleLocale: () => void
  t: (key: ShellCopyKey) => string
}

const defaultContext: ShellI18nContextValue = {
  locale: DEFAULT_SHELL_LOCALE,
  setLocale: () => {},
  toggleLocale: () => {},
  t: (key) => SHELL_COPY[DEFAULT_SHELL_LOCALE][key] ?? key,
}

const ShellI18nContext = createContext<ShellI18nContextValue>(defaultContext)

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
    const t = (key: ShellCopyKey) => SHELL_COPY[locale][key] ?? key
    return { locale, setLocale, toggleLocale, t }
  }, [locale, setLocale])

  return <ShellI18nContext.Provider value={value}>{children}</ShellI18nContext.Provider>
}

export function useShellI18n() {
  return useContext(ShellI18nContext)
}
