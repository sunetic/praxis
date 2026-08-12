import { render, type RenderOptions } from "@testing-library/react"
import type { ReactElement, ReactNode } from "react"

import { ShellI18nProvider, type ShellLocale } from "@/i18n/shellI18n"

type ShellRenderOptions = Omit<RenderOptions, "wrapper"> & {
  locale?: ShellLocale
}

export function renderWithShell(
  ui: ReactElement,
  { locale = "zh-CN", ...options }: ShellRenderOptions = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return <ShellI18nProvider initialLocale={locale}>{children}</ShellI18nProvider>
  }

  return render(ui, { wrapper: Wrapper, ...options })
}
