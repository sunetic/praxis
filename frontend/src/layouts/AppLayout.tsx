import { Outlet, useLocation } from "react-router-dom"
import { Sidebar } from "@/components/layout/Sidebar"
import { useShellI18n } from "@/i18n/shellI18n"

export function AppLayout() {
  const location = useLocation()
  const { locale } = useShellI18n()
  const isPageWorkspaceRoute =
    location.pathname === "/page" || location.pathname.startsWith("/page/workspace/")

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className={`min-w-0 flex-1 overflow-y-auto overflow-x-hidden ${isPageWorkspaceRoute ? "p-0" : "p-3 min-[901px]:p-6"}`}>
        <Outlet key={locale} />
      </main>
    </div>
  )
}
