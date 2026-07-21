import type { LucideIcon } from "lucide-react"
import { Activity, AlertTriangle, Sparkles, Users, Zap } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

type SessionTransactionOverviewProps = {
  sessionsTotal: number
  sessionsActive: number
  longTxnCount: number
  pendingTxnCount: number
  topUsers: Array<[string, number]>
  topIps: Array<[string, number]>
  onOpenAiDrawer: () => void
}

type StatusStyle = {
  icon: LucideIcon
  text: string
  bg: string
  iconColor: string
}

const statusStyles: Record<string, StatusStyle> = {
  info: { icon: Users, text: "text-primary", bg: "bg-primary/15", iconColor: "text-primary" },
  positive: { icon: Activity, text: "text-positive", bg: "bg-positive/15", iconColor: "text-positive" },
  warning: { icon: AlertTriangle, text: "text-warning", bg: "bg-warning/15", iconColor: "text-warning" },
  negative: { icon: Zap, text: "text-negative", bg: "bg-negative/15", iconColor: "text-negative" },
}

const staggerDelay = ["", "delay-75", "delay-150", "delay-200"]

export function SessionTransactionOverview({
  sessionsTotal,
  sessionsActive,
  longTxnCount,
  pendingTxnCount,
  topUsers,
  topIps,
  onOpenAiDrawer,
}: SessionTransactionOverviewProps) {
  const activeShare = sessionsTotal > 0 ? Math.round((sessionsActive / sessionsTotal) * 100) : 0
  const longTxnShare = sessionsTotal > 0 ? Math.round((longTxnCount / sessionsTotal) * 100) : 0
  const pendingTxnShare = sessionsTotal > 0 ? Math.round((pendingTxnCount / sessionsTotal) * 100) : 0

  const cards = [
    {
      key: "total",
      title: "总会话数",
      value: sessionsTotal,
      hint: sessionsTotal > 0 ? `当前范围内共 ${sessionsTotal} 个连接` : "当前范围内暂无连接",
      style: statusStyles.info,
    },
    {
      key: "active",
      title: "活跃会话",
      value: sessionsActive,
      hint: sessionsTotal > 0 ? `活跃占比 ${activeShare}%` : "等待会话结果",
      style: statusStyles.positive,
    },
    {
      key: "long",
      title: "长事务",
      value: longTxnCount,
      hint: longTxnCount > 0 ? `风险占比 ${longTxnShare}%` : "当前未发现长事务",
      style: longTxnCount > 0 ? statusStyles.warning : statusStyles.info,
    },
    {
      key: "pending",
      title: "未提交事务",
      value: pendingTxnCount,
      hint: pendingTxnCount > 0 ? `风险占比 ${pendingTxnShare}%` : "当前没有未提交事务",
      style: pendingTxnCount > 0 ? statusStyles.negative : statusStyles.info,
    },
  ]

  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 duration-500 space-y-4">
      <section className="space-y-3" aria-label="会话事务指标卡片区">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium text-foreground">会话与事务概览</div>
            <div className="text-xs text-muted-foreground">
              先看连接规模、活跃压力与事务堆积，再决定是否下钻会话或事务明细。
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-8 shrink-0 gap-1.5 text-xs"
            onClick={onOpenAiDrawer}
            aria-label="AI 诊断"
          >
            <Sparkles className="size-3.5" />
            AI 诊断
          </Button>
        </div>
        <section className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          {cards.map((card, index) => {
            const Icon = card.style.icon
            return (
              <Card
                key={card.key}
                className={cn(
                  "hover:shadow-md hover:border-border/60 transition-all duration-300",
                  "animate-in fade-in slide-in-from-bottom-1 duration-500",
                  staggerDelay[index] ?? ""
                )}
              >
                <CardContent className="p-3.5">
                  <div className="flex items-center gap-2">
                    <div className={cn("flex size-7 shrink-0 items-center justify-center rounded-md", card.style.bg)}>
                      <Icon className={cn("size-3.5", card.style.iconColor)} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className={cn("text-lg font-semibold tabular-nums leading-none", card.style.text)}>
                        {card.value}
                      </p>
                      <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{card.title}</p>
                    </div>
                  </div>
                  <p className="mt-1.5 text-[11px] text-muted-foreground">{card.hint}</p>
                </CardContent>
              </Card>
            )
          })}
        </section>
      </section>

      <div className="grid gap-4 sm:grid-cols-2">
        <DistributionCard title="高频用户" rows={topUsers} emptyText="暂无用户分布" />
        <DistributionCard title="高频来源 IP" rows={topIps} emptyText="暂无来源分布" />
      </div>
    </div>
  )
}

function DistributionCard({
  title,
  rows,
  emptyText,
}: {
  title: string
  rows: Array<[string, number]>
  emptyText: string
}) {
  const maxValue = rows[0]?.[1] ?? 0
  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="text-sm font-medium text-foreground">{title}</div>
        {rows.length === 0 ? (
          <div className="text-sm text-muted-foreground">{emptyText}</div>
        ) : (
          rows.map(([label, value]) => (
            <div key={label} className="space-y-1">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate text-foreground">{label}</span>
                <span className="tabular-nums text-muted-foreground">{value}</span>
              </div>
              <div className="h-2 rounded-full bg-muted">
                <div
                  className="h-2 rounded-full bg-primary/70"
                  style={{ width: `${Math.max(10, Math.round((value / maxValue) * 100))}%` }}
                />
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}
