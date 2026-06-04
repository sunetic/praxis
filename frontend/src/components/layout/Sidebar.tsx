import { NavLink, useLocation } from "react-router-dom"
import type { LucideIcon } from "lucide-react"
import { MessageSquare, Database, Bot, Sparkles, FunctionSquare, CalendarClock, Wrench, Send, Search, Blocks, Settings, BookOpen, Globe } from "lucide-react"
import { cn } from "@/lib/utils"
import { useShellI18n, type ShellCopyKey } from "@/i18n/shellI18n"

type NavItem = {
  to: string
  icon: LucideIcon
  labelKey: ShellCopyKey
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
      { to: "/knowledge", icon: BookOpen, labelKey: "sidebar.nav.knowledge" },
      { to: "/channel", icon: Send, labelKey: "sidebar.nav.channel" },
    ],
  },
  {
    labelKey: "sidebar.group.buildRuntime",
    items: [
      { to: "/function", icon: FunctionSquare, labelKey: "sidebar.nav.function" },
      { to: "/scheduler", icon: CalendarClock, labelKey: "sidebar.nav.scheduler" },
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

  return (
    <aside className="w-56 shrink-0 border-r border-gray-100 bg-white flex flex-col">
      <div className="flex h-14 items-center gap-2.5 px-4 border-b border-gray-100">
        <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-100">
          <Sparkles className="size-4 text-indigo-500" />
        </div>
        <span className="text-lg font-semibold text-gray-800">{t("sidebar.brand")}</span>
      </div>
      <nav className="flex-1 overflow-y-auto p-3">
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
            {group.items.map((item) => (
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
            ))}
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
