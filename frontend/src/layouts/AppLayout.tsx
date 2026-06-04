import { Outlet, useLocation } from "react-router-dom"
import { Sidebar } from "@/components/layout/Sidebar"

export function AppLayout() {
  const location = useLocation()
  const isPageWorkspaceRoute =
    location.pathname === "/page" || location.pathname.startsWith("/page/workspace/")

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className={`flex-1 overflow-y-auto overflow-x-hidden ${isPageWorkspaceRoute ? "p-0" : "p-6"}`}>
        <Outlet />
      </main>
    </div>
  )
}
