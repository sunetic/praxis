import { useEffect, useMemo, useState } from "react"
import { Link, NavLink, useLocation } from "react-router-dom"
import type { LucideIcon } from "lucide-react"
import { MessageSquare, Database, Bot, Sparkles, File, FunctionSquare, CalendarClock, Wrench, Send, Blocks, Settings, Plug, BookOpen, Globe } from "lucide-react"
import { cn } from "@/lib/utils"
import { pagesApi } from "@/lib/api"
import { useShellI18n, type ShellCopyKey } from "@/i18n/shellI18n"

type PublishedPage = {
  id: number
  name?: string | null
  path?: string | null
  status?: string | null
}

type NavItem = {
  to: string
  icon: LucideIcon
  labelKey: ShellCopyKey
  kind?: "link" | "page"
}

type NavGroup = {
  labelKey: ShellCopyKey
  items: NavItem[]
}

const navGroups: NavGroup[] = [
  {
    labelKey: "sidebar.group.aiWorkspace",
    items: [
      { to: "/chat", icon: MessageSquare, labelKey: "sidebar.nav.chat" },
      { to: "/agent", icon: Bot, labelKey: "sidebar.nav.agent" },
      { to: "/skills", icon: Wrench, labelKey: "sidebar.nav.skill" },
    ],
  },
  {
    labelKey: "sidebar.group.connectivity",
    items: [
      { to: "/datasource", icon: Database, labelKey: "sidebar.nav.datasource" },
      { to: "/service", icon: Plug, labelKey: "sidebar.nav.service" },
      { to: "/knowledge", icon: BookOpen, labelKey: "sidebar.nav.knowledge" },
      { to: "/channel", icon: Send, labelKey: "sidebar.nav.channel" },
    ],
  },
  {
    labelKey: "sidebar.group.buildRuntime",
    items: [
      { to: "/function", icon: FunctionSquare, labelKey: "sidebar.nav.function" },
      { to: "/scheduler", icon: CalendarClock, labelKey: "sidebar.nav.scheduler" },
      { to: "/page", icon: File, labelKey: "sidebar.nav.page", kind: "page" },
    ],
  },
  {
    labelKey: "sidebar.group.system",
    items: [
      { to: "/capabilities", icon: Blocks, labelKey: "sidebar.nav.capabilities" },
      { to: "/settings", icon: Settings, labelKey: "sidebar.nav.settings" },
    ],
  },
]

export function Sidebar() {
  const { locale, toggleLocale, t } = useShellI18n()
  const location = useLocation()
  const [publishedPages, setPublishedPages] = useState<PublishedPage[]>([])
  const [pageExpanded, setPageExpanded] = useState(location.pathname.startsWith("/page"))
  const isPageRoute = location.pathname.startsWith("/page")

  useEffect(() => {
    let cancelled = false
    const loadNavigation = () => {
      pagesApi
        .navigation()
        .then((res) => {
          if (cancelled) return
          const rows = Array.isArray(res) ? res : []
          setPublishedPages(
            rows.filter((item) => String(item?.status || "").toLowerCase() === "published")
          )
        })
        .catch(() => {
          if (!cancelled) setPublishedPages([])
        })
    }
    loadNavigation()
    const onRefresh = () => loadNavigation()
    window.addEventListener("page-navigation-refresh", onRefresh)
    return () => {
      cancelled = true
      window.removeEventListener("page-navigation-refresh", onRefresh)
    }
  }, [location.pathname])

  const activePageId = useMemo(() => {
    const parts = location.pathname.split("/")
    if (parts[1] !== "page") return null
    const maybeId = parts[2] === "workspace" ? Number(parts[3]) : Number(parts[2])
    return Number.isInteger(maybeId) && maybeId > 0 ? maybeId : null
  }, [location.pathname])

  const isPageExpanded = pageExpanded || isPageRoute

  const renderPageItem = (item: NavItem) => (
    <div key={item.to} className="space-y-1">
      <Link
        to={item.to}
        onClick={() => setPageExpanded(true)}
        className={cn(
          "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors",
          isPageRoute
            ? "bg-indigo-50 text-indigo-600"
            : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
        )}
      >
        <item.icon className="size-4" />
        {t(item.labelKey)}
      </Link>
      <div
        className={cn(
          "ml-6 overflow-hidden pr-1 transition-[max-height,opacity] duration-200 ease-out",
          isPageExpanded ? "max-h-44 opacity-100" : "max-h-0 opacity-0"
        )}
      >
        <div className="max-h-44 overflow-y-auto space-y-0.5">
          {publishedPages.length === 0 ? (
            <div className="rounded-lg px-2 py-1.5 text-xs text-gray-400">{t("sidebar.emptyPublishedPage")}</div>
          ) : (
            publishedPages.map((page) => (
              <Link
                key={page.id}
                to={typeof page.path === "string" && page.path.trim() ? page.path : `/page/${page.id}`}
                className={cn(
                  "block rounded-lg px-2 py-1.5 text-xs transition-colors",
                  activePageId === page.id
                    ? "bg-indigo-100 text-indigo-700"
                    : "text-gray-500 hover:bg-gray-50 hover:text-gray-700"
                )}
              >
                {page.name || `${t("sidebar.nav.page")} ${page.id}`}
              </Link>
            ))
          )}
        </div>
      </div>
    </div>
  )

  return (
    <aside className="w-56 shrink-0 border-r border-gray-100 bg-white flex flex-col">
      <div className="flex h-14 items-center gap-2.5 px-4 border-b border-gray-100">
        <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-100">
          <Sparkles className="size-4 text-indigo-500" />
        </div>
        <span className="text-lg font-semibold text-gray-800">{t("sidebar.brand")}</span>
      </div>
      <nav className="flex-1 p-3">
        {navGroups.map((group, groupIndex) => (
          <div
            key={group.labelKey}
            role="group"
            aria-label={t(group.labelKey)}
            className={cn("space-y-1", groupIndex > 0 && "mt-3 border-t border-gray-100 pt-3")}
          >
            <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-gray-400">
              {t(group.labelKey)}
            </div>
            {group.items.map((item) =>
              item.kind === "page" ? (
                renderPageItem(item)
              ) : (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-indigo-50 text-indigo-600"
                        : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                    )
                  }
                >
                  <item.icon className="size-4" />
                  {t(item.labelKey)}
                </NavLink>
              )
            )}
          </div>
        ))}
      </nav>
      <div className="border-t border-gray-100 p-3">
        <button
          aria-label={t("topbar.locale.switchAria")}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-gray-500 transition-colors hover:bg-gray-50 hover:text-gray-700"
          onClick={toggleLocale}
          type="button"
        >
          <Globe className="size-4" />
          {locale === "zh-CN" ? t("topbar.locale.switchToEn") : t("topbar.locale.switchToZh")}
        </button>
      </div>
    </aside>
  )
}
