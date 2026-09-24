import { createContext, useContext } from "react"

import { SHELL_EN_US_COPY } from "./shellCopyEn"
import { SHELL_ZH_CN_COPY, type ShellCopyKey } from "./shellCopyZh"

export type { ShellCopyKey } from "./shellCopyZh"

export type ShellLocale = "zh-CN" | "en-US"
export type ShellTranslatorFn = (key: ShellCopyKey) => string

export const SHELL_LOCALE_STORAGE_KEY = "praxis.locale"
export const DEFAULT_SHELL_LOCALE: ShellLocale = "en-US"

export const SHELL_COPY: Record<ShellLocale, Record<ShellCopyKey, string>> = {
  "zh-CN": SHELL_ZH_CN_COPY,
  "en-US": SHELL_EN_US_COPY,
}

export function normalizeLocale(value: unknown): ShellLocale {
  if (typeof value === "string" && value in SHELL_COPY) return value as ShellLocale
  return DEFAULT_SHELL_LOCALE
}

export function readStoredLocale(): ShellLocale {
  if (typeof window === "undefined") return DEFAULT_SHELL_LOCALE
  return normalizeLocale(window.localStorage.getItem(SHELL_LOCALE_STORAGE_KEY))
}

export type ShellI18nContextValue = {
  locale: ShellLocale
  setLocale: (next: ShellLocale) => void
  toggleLocale: () => void
  t: ShellTranslatorFn
}

const defaultContext: ShellI18nContextValue = {
  locale: DEFAULT_SHELL_LOCALE,
  setLocale: () => {},
  toggleLocale: () => {},
  t: (key) => SHELL_COPY[DEFAULT_SHELL_LOCALE][key] ?? key,
}

export const ShellI18nContext = createContext<ShellI18nContextValue>(defaultContext)

export function useShellI18n(): ShellI18nContextValue {
  return useContext(ShellI18nContext)
}
