import { Suspense } from "react"
import { Outlet, useLocation } from "react-router-dom"
import { Loader2 } from "lucide-react"

import { Sidebar } from "@/components/layout/Sidebar"
import { useShellI18n } from "@/i18n/shellI18nContext"

export function AppLayout() {
  const location = useLocation()
  const { locale, t } = useShellI18n()
  const isPageWorkspaceRoute =
    location.pathname === "/page" || location.pathname.startsWith("/page/workspace/")

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className={`min-w-0 flex-1 overflow-y-auto overflow-x-hidden ${isPageWorkspaceRoute ? "p-0" : "p-3 min-[901px]:p-6"}`}>
        <Suspense
          fallback={
            <div className="flex min-h-48 items-center justify-center" role="status">
              <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />
              <span className="sr-only">{t("ui.loading")}</span>
            </div>
          }
        >
          <Outlet key={locale} />
        </Suspense>
      </main>
    </div>
  )
}
