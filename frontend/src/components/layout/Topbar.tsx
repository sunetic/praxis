import { Search, Bell, ChevronDown } from "lucide-react"
import { useShellI18n } from "@/i18n/shellI18n"

export function Topbar() {
  const { locale, toggleLocale, t } = useShellI18n()

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-background px-6">
      <div />
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-1.5 text-sm text-muted-foreground">
          <Search className="size-4" />
          <span>{t("topbar.searchPlaceholder")}</span>
        </div>
        <button
          aria-label={t("topbar.locale.switchAria")}
          className="rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
          onClick={toggleLocale}
          type="button"
        >
          {locale === "zh-CN" ? t("topbar.locale.switchToEn") : t("topbar.locale.switchToZh")}
        </button>
        <button className="rounded-lg p-2 transition-colors hover:bg-muted">
          <Bell className="size-4 text-muted-foreground" />
        </button>
        <div className="flex items-center gap-2 pl-2">
          <div className="flex size-8 items-center justify-center rounded-full bg-primary/10 text-sm font-medium text-primary">
            A
          </div>
          <div className="text-left">
            <div className="text-sm font-medium text-foreground">{t("topbar.user.name")}</div>
            <div className="text-xs text-muted-foreground">{t("topbar.user.role")}</div>
          </div>
          <ChevronDown className="size-4 text-muted-foreground" />
        </div>
      </div>
    </header>
  )
}
