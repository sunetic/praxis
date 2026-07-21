import { useMemo } from "react"
import type { LucideIcon } from "lucide-react"
import { Activity, AlertTriangle, CheckCircle, ChevronRight, XCircle } from "lucide-react"

import type { StatsTenantConfigCheck, StatsWorkbenchCard } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

type StatusStyle = {
  icon: LucideIcon
  text: string
  bg: string
  iconColor: string
}

const statusStyles: Record<string, StatusStyle> = {
  healthy: { icon: CheckCircle, text: "text-positive", bg: "bg-positive/15", iconColor: "text-positive" },
  warning: { icon: AlertTriangle, text: "text-warning", bg: "bg-warning/15", iconColor: "text-warning" },
  critical: { icon: XCircle, text: "text-negative", bg: "bg-negative/15", iconColor: "text-negative" },
  info: { icon: Activity, text: "text-primary", bg: "bg-primary/15", iconColor: "text-primary" },
}

const fallbackStyle: StatusStyle = statusStyles.info

const staggerDelay = ["", "delay-75", "delay-150", "delay-200"]

/** Classify a config check as healthy vs issue without exposing backend enum values. */
function isHealthyCheck(check: StatsTenantConfigCheck): boolean {
  return check.issue_type === "healthy"
}

type StatsOverviewCardsProps = {
  cards: StatsWorkbenchCard[]
  configChecks: StatsTenantConfigCheck[]
  warnings: string[]
  onTenantConfigClick?: (check: StatsTenantConfigCheck) => void
}

export function StatsOverviewCards({ cards, configChecks, warnings, onTenantConfigClick }: StatsOverviewCardsProps) {
  const { issues: issueChecks, healthyCount, allHealthy } = useMemo(() => {
    const issues = configChecks.filter((c) => !isHealthyCheck(c))
    return {
      issues,
      healthyCount: configChecks.length - issues.length,
      allHealthy: configChecks.length > 0 && issues.length === 0,
    }
  }, [configChecks])
  const primaryConfigCheck = issueChecks[0] ?? configChecks[0] ?? null

  return (
    <div className="space-y-4">
      <section className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card, index) => {
          const s = statusStyles[card.status] ?? fallbackStyle
          const Icon = s.icon
          const isSchedulerCard = card.key === "scheduler"
          const canOpenConfig = isSchedulerCard && !!primaryConfigCheck && !!onTenantConfigClick
          const detailText = !isSchedulerCard || !configChecks.length
            ? null
            : allHealthy
              ? `${healthyCount} 个租户配置正常，点击查看详情`
              : `${issueChecks.length} 个租户需关注${healthyCount > 0 ? `，${healthyCount} 个正常` : ""}，点击查看详情`
          const secondaryText = card.hint || detailText
          const cardBody = (
            <CardContent className="p-3.5">
              <div className="flex items-center gap-2">
                <div className={cn("flex size-7 shrink-0 items-center justify-center rounded-md", s.bg)}>
                  <Icon className={cn("size-3.5", s.iconColor)} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className={cn("text-lg font-semibold tabular-nums leading-none", s.text)}>
                    {card.value}
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground truncate">{card.title}</p>
                </div>
                {canOpenConfig ? <ChevronRight className="size-3 shrink-0 text-muted-foreground" /> : null}
              </div>
              {secondaryText ? (
                <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground overflow-hidden">
                  <span className="truncate">{secondaryText}</span>
                  {card.hint && detailText ? <span className="shrink-0 text-muted-foreground/70">·</span> : null}
                  {card.hint && detailText ? <span className="truncate">{detailText}</span> : null}
                </p>
              ) : null}
            </CardContent>
          )
          return canOpenConfig ? (
            <Card
              key={card.key}
              className={cn(
                "cursor-pointer hover:shadow-md hover:border-border/60 transition-all duration-300",
                "animate-in fade-in slide-in-from-bottom-1 duration-500",
                staggerDelay[index] ?? ""
              )}
              onClick={() => primaryConfigCheck && onTenantConfigClick?.(primaryConfigCheck)}
            >
              {cardBody}
            </Card>
          ) : (
            <Card
              key={card.key}
              className={cn(
                "hover:shadow-md hover:border-border/60 transition-all duration-300",
                "animate-in fade-in slide-in-from-bottom-1 duration-500",
                staggerDelay[index] ?? ""
              )}
            >
              {cardBody}
            </Card>
          )
        })}
      </section>

      {warnings.length > 0 ? (
        <Card className="animate-in fade-in slide-in-from-bottom-1 duration-500 delay-200">
          <CardContent className="p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
              <div className="space-y-1 text-sm text-foreground">
                {warnings.map((warning, index) => (
                  <p key={`${warning}-${index}`}>{warning}</p>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
