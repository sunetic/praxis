import { useEffect, useMemo, useState, useRef, useCallback } from "react"
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  Code2,
  Database,
  Filter,
  Loader2,
  MessageSquare,
  RefreshCw,
  Search,
  Send,
  Server,
  ShieldAlert,
  Sparkles,
  Table2,
  TrendingUp,
} from "lucide-react"
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Drawer, DrawerClose, DrawerContent, DrawerHeader, DrawerTitle } from "@/components/ui/drawer"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { cn } from "@/lib/utils"

// ─── Mock Data ────────────────────────────────────────────────────────────────

const MOCK_CLUSTERS = ["cn-hangzhou-prod", "cn-shanghai-pre", "us-west-1"]

const MOCK_TREND_DATA = Array.from({ length: 24 }, (_, i) => ({
  hour: `${String(i).padStart(2, "0")}:00`,
  healthy: Math.floor(Math.random() * 40 + 160),
  warning: Math.floor(Math.random() * 15 + 5),
  critical: Math.floor(Math.random() * 8),
}))

const MOCK_DISTRIBUTION_DATA = [
  { name: "周一", healthy: 162, risk: 18 },
  { name: "周二", healthy: 175, risk: 14 },
  { name: "周三", healthy: 158, risk: 22 },
  { name: "周四", healthy: 180, risk: 11 },
  { name: "周五", healthy: 171, risk: 19 },
  { name: "周六", healthy: 145, risk: 8 },
  { name: "周日", healthy: 138, risk: 6 },
]

type SeverityLevel = "healthy" | "warning" | "critical" | "info"

type MockIssue = {
  id: number
  object: string
  cluster: string
  tenant: string
  severity: SeverityLevel
  kind: string
  summary: string
  updatedAt: string
  score: number
}

const SEVERITY_CONFIG: Record<SeverityLevel, { label: string; variant: "default" | "secondary" | "destructive" | "outline"; dotBg: string }> = {
  healthy: { label: "正常", variant: "secondary", dotBg: "bg-positive" },
  warning: { label: "警告", variant: "outline", dotBg: "bg-warning" },
  critical: { label: "严重", variant: "destructive", dotBg: "bg-negative" },
  info: { label: "信息", variant: "secondary", dotBg: "bg-primary" },
}

const MOCK_ISSUES: MockIssue[] = Array.from({ length: 86 }, (_, i) => {
  const severities: SeverityLevel[] = ["critical", "warning", "warning", "healthy", "healthy", "healthy", "info"]
  const kinds = ["failed_table", "stale_stats", "dml_change", "scheduling"]
  const kindLabels: Record<string, string> = {
    failed_table: "收集失败",
    stale_stats: "统计过期",
    dml_change: "DML 变更",
    scheduling: "调度异常",
  }
  const severity = severities[i % severities.length]
  const kind = kinds[i % kinds.length]
  return {
    id: 1000 + i,
    object: `db_${(i % 5) + 1}.t_order_${String(i + 1).padStart(3, "0")}`,
    cluster: MOCK_CLUSTERS[i % MOCK_CLUSTERS.length],
    tenant: `tenant_${(i % 3) + 1}`,
    severity,
    kind: kindLabels[kind],
    summary: [
      "最近 3 次收集均失败，错误码 4013",
      "统计信息已过期 72h，存在执行计划退化风险",
      "24h 内 DML 变更量超过阈值，建议重新收集",
      "调度任务连续 2 次超时，最近窗口未执行",
      "直方图分布严重偏斜，Top-N 占比 > 85%",
      "列基数估算偏差率达 47%，可能影响 Join 选择",
    ][i % 6],
    updatedAt: new Date(Date.now() - Math.random() * 86400000 * 3).toISOString().slice(0, 16).replace("T", " "),
    score: Math.floor(Math.random() * 100),
  }
})

type MockDrawerDetail = {
  title: string
  subtitle: string
  sections: Array<{
    key: string
    title: string
    fields: Array<{ label: string; value: string }>
  }>
  timeline: Array<{
    time: string
    status: string
    detail: string
  }>
}

function getMockDrawerDetail(issue: MockIssue): MockDrawerDetail {
  return {
    title: issue.object,
    subtitle: `${issue.cluster} / ${issue.tenant}`,
    sections: [
      {
        key: "basic",
        title: "基本信息",
        fields: [
          { label: "对象名称", value: issue.object },
          { label: "所属集群", value: issue.cluster },
          { label: "租户", value: issue.tenant },
          { label: "问题类型", value: issue.kind },
          { label: "严重程度", value: SEVERITY_CONFIG[issue.severity].label },
          { label: "风险分数", value: `${issue.score}/100` },
        ],
      },
      {
        key: "diagnosis",
        title: "诊断详情",
        fields: [
          { label: "问题摘要", value: issue.summary },
          { label: "首次发现", value: "2026-04-05 14:32" },
          { label: "最近更新", value: issue.updatedAt },
          { label: "影响范围", value: "3 个慢查询关联此表" },
        ],
      },
    ],
    timeline: [
      { time: "04-07 10:30", status: "failed", detail: "收集超时，耗时 > 300s" },
      { time: "04-07 06:00", status: "failed", detail: "错误码 4013：锁冲突" },
      { time: "04-06 22:00", status: "success", detail: "正常完成，耗时 12s" },
      { time: "04-06 18:00", status: "success", detail: "正常完成，耗时 9s" },
      { time: "04-06 14:00", status: "failed", detail: "内存不足，OOM killed" },
    ],
  }
}

// ─── Sparkline ────────────────────────────────────────────────────────────────

function generateSparkline(points: number, base: number, variance: number, trend: "up" | "down" | "flat" = "flat") {
  return Array.from({ length: points }, (_, i) => {
    const trendOffset = trend === "up" ? i * (variance / points) : trend === "down" ? -i * (variance / points) : 0
    return { v: Math.max(0, base + trendOffset + (Math.random() - 0.5) * variance) }
  })
}

function Sparkline({ data, color, className }: { data: Array<{ v: number }>; color: string; className?: string }) {
  return (
    <div className={cn("h-9 w-20 opacity-80", className)}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <defs>
            <linearGradient id={`spark-${color.replace(/[^a-z0-9]/gi, "")}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#spark-${color.replace(/[^a-z0-9]/gi, "")})`}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

const SPARKLINE_DATA = {
  total: generateSparkline(12, 29, 8, "flat"),
  critical: generateSparkline(12, 5, 3, "up"),
  warning: generateSparkline(12, 8, 4, "down"),
  healthy: generateSparkline(12, 12, 3, "flat"),
}

// ─── Animated Number ──────────────────────────────────────────────────────────

function AnimatedNumber({ value, duration = 600 }: { value: number; duration?: number }) {
  const [display, setDisplay] = useState(0)
  const rafRef = useRef<number>(0)

  useEffect(() => {
    const start = performance.now()
    const from = display
    const to = value

    function tick(now: number) {
      const elapsed = now - start
      const progress = Math.min(elapsed / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setDisplay(Math.round(from + (to - from) * eased))
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick)
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, duration])

  return <>{display.toLocaleString()}</>
}

// ─── Stat Card ────────────────────────────────────────────────────────────────

type AccentKey = "primary" | "positive" | "negative" | "warning"

const ACCENT_STYLES: Record<AccentKey, { text: string; bg: string; iconColor: string; cssColor: string }> = {
  primary: { text: "text-primary", bg: "bg-primary/15", iconColor: "text-primary", cssColor: "#6366f1" },
  positive: { text: "text-positive", bg: "bg-positive/15", iconColor: "text-positive", cssColor: "#4d7c5a" },
  negative: { text: "text-negative", bg: "bg-negative/15", iconColor: "text-negative", cssColor: "#c75c5c" },
  warning: { text: "text-warning", bg: "bg-warning/15", iconColor: "text-warning", cssColor: "#e5a54b" },
}

type StatCardProps = {
  title: string
  value: number
  change?: number
  icon: React.ReactNode
  accent?: AccentKey
  delay?: number
  sparkData?: Array<{ v: number }>
}

function StatCard({ title, value, change, icon, accent = "primary", delay = 0, sparkData }: StatCardProps) {
  const [visible, setVisible] = useState(false)
  const s = ACCENT_STYLES[accent]

  useEffect(() => {
    const timer = setTimeout(() => setVisible(true), delay)
    return () => clearTimeout(timer)
  }, [delay])

  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-lg border border-border bg-card shadow-sm transition-all duration-300",
        "hover:shadow-md hover:border-border/60",
        visible ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"
      )}
      style={{ transitionDelay: `${delay}ms` }}
    >
      <div className="p-5">
        <div className="flex items-start justify-between">
          <div className="min-w-0 flex-1 space-y-2">
            <p className="text-xs font-medium tracking-wide text-muted-foreground">{title}</p>
            <div className="flex items-end gap-3">
              <p className="text-2xl font-semibold tabular-nums text-foreground">
                <AnimatedNumber value={value} />
              </p>
              {sparkData ? <Sparkline data={sparkData} color={s.cssColor} /> : null}
            </div>
            {change !== undefined ? (
              <div className={cn(
                "flex items-center gap-1 text-xs font-medium",
                change >= 0 ? "text-positive" : "text-negative"
              )}>
                {change >= 0 ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}
                <span>{Math.abs(change)}% vs 昨日</span>
              </div>
            ) : null}
          </div>
          <div className={cn("flex size-10 shrink-0 items-center justify-center rounded-lg transition-all duration-200 group-hover:scale-105", s.bg)}>
            <div className={s.iconColor}>{icon}</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Chart Tooltip ────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-border/60 bg-card px-3 py-2 shadow-md">
      <p className="mb-1.5 text-xs font-medium text-foreground">{label}</p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2 text-xs">
          <span className="size-2 rounded-full" style={{ backgroundColor: entry.color }} />
          <span className="text-muted-foreground">{entry.name}</span>
          <span className="ml-auto font-medium tabular-nums text-foreground">{entry.value}</span>
        </div>
      ))}
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 10
const TAB_OVERVIEW = "overview"
const TAB_RISK = "risk"
type ActiveTab = typeof TAB_OVERVIEW | typeof TAB_RISK

export function StatsAnalysisTemplatePage() {
  // ── State ───────────────────────────────────────────────────────────────────
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedCluster, setSelectedCluster] = useState(MOCK_CLUSTERS[0])
  const [activeTab, setActiveTab] = useState<ActiveTab>(TAB_OVERVIEW)
  const [page, setPage] = useState(1)
  const [selectedIssue, setSelectedIssue] = useState<MockIssue | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerMode, setDrawerMode] = useState<"info" | "chat">("info")
  const [refreshing, setRefreshing] = useState(false)
  const [mockChatInput, setMockChatInput] = useState("")

  // ── Simulate loading ────────────────────────────────────────────────────────
  useEffect(() => {
    const timer = setTimeout(() => setLoading(false), 1200)
    return () => clearTimeout(timer)
  }, [])

  // ── Filtered data ───────────────────────────────────────────────────────────
  const filteredIssues = useMemo(() => {
    let items = MOCK_ISSUES.filter((item) => item.cluster === selectedCluster)
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      items = items.filter(
        (item) => item.object.toLowerCase().includes(q) || item.summary.toLowerCase().includes(q)
      )
    }
    if (activeTab === TAB_RISK) {
      items = items.filter((item) => item.severity === "critical" || item.severity === "warning")
    }
    return items
  }, [selectedCluster, searchQuery, activeTab])

  const pagedIssues = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filteredIssues.slice(start, start + PAGE_SIZE)
  }, [filteredIssues, page])

  // ── Reset page on filter change ─────────────────────────────────────────────
  useEffect(() => {
    setPage(1)
  }, [selectedCluster, searchQuery, activeTab])

  // ── Handlers ────────────────────────────────────────────────────────────────
  const handleRefresh = useCallback(() => {
    setRefreshing(true)
    setTimeout(() => setRefreshing(false), 800)
  }, [])

  const openDetail = useCallback((issue: MockIssue) => {
    setSelectedIssue(issue)
    setDrawerOpen(true)
  }, [])

  const drawerDetail = selectedIssue ? getMockDrawerDetail(selectedIssue) : null

  // ── Stats summary ───────────────────────────────────────────────────────────
  const clusterIssues = useMemo(
    () => MOCK_ISSUES.filter((item) => item.cluster === selectedCluster),
    [selectedCluster]
  )
  const criticalCount = clusterIssues.filter((item) => item.severity === "critical").length
  const warningCount = clusterIssues.filter((item) => item.severity === "warning").length
  const healthyCount = clusterIssues.filter((item) => item.severity === "healthy").length

  // ── Toolbar ─────────────────────────────────────────────────────────────────
  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <NativeSelect
          aria-label="集群范围"
          className="w-44 bg-card"
          value={selectedCluster}
          onChange={(e) => setSelectedCluster(e.target.value)}
        >
          {MOCK_CLUSTERS.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </NativeSelect>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            placeholder="搜索对象或问题..."
            className="w-72 rounded-lg bg-card pl-9 text-sm"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button
          variant="outline"
          size="sm"
          onClick={handleRefresh}
          disabled={refreshing}
        >
          {refreshing ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  // ── Overview Cards ──────────────────────────────────────────────────────────
  function renderStatCards() {
    if (loading) {
      return (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="animate-in fade-in overflow-hidden rounded-lg border border-border bg-card shadow-sm duration-500"
              style={{ animationDelay: `${i * 80}ms` }}
            >
              <div className="p-5">
                <Skeleton className="mb-3 h-3 w-24" />
                <div className="flex items-end gap-3">
                  <Skeleton className="h-7 w-14" />
                  <Skeleton className="h-8 w-20 rounded" />
                </div>
                <Skeleton className="mt-3 h-3 w-20" />
              </div>
            </div>
          ))}
        </div>
      )
    }

    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title="监控对象总数"
          value={clusterIssues.length}
          icon={<Database className="size-5" />}
          accent="primary"
          sparkData={SPARKLINE_DATA.total}
          delay={0}
        />
        <StatCard
          title="严重告警"
          value={criticalCount}
          change={12}
          icon={<ShieldAlert className="size-5" />}
          accent="negative"
          sparkData={SPARKLINE_DATA.critical}
          delay={80}
        />
        <StatCard
          title="风险预警"
          value={warningCount}
          change={-5}
          icon={<AlertTriangle className="size-5" />}
          accent="warning"
          sparkData={SPARKLINE_DATA.warning}
          delay={160}
        />
        <StatCard
          title="健康对象"
          value={healthyCount}
          icon={<Activity className="size-5" />}
          accent="positive"
          sparkData={SPARKLINE_DATA.healthy}
          delay={240}
        />
      </div>
    )
  }

  // ── Charts ──────────────────────────────────────────────────────────────────
  function renderCharts() {
    if (loading) {
      return (
        <div className="grid gap-4 lg:grid-cols-5">
          <div className="animate-in fade-in rounded-lg border border-border bg-card p-5 shadow-sm duration-500 lg:col-span-3" style={{ animationDelay: "200ms" }}>
            <div className="mb-4 flex items-center gap-2">
              <Skeleton className="size-4 rounded" />
              <Skeleton className="h-4 w-20" />
            </div>
            <div className="flex items-end gap-1">
              {Array.from({ length: 24 }).map((_, i) => (
                <Skeleton
                  key={i}
                  className="flex-1 rounded-t"
                  style={{ height: `${40 + Math.random() * 140}px` }}
                />
              ))}
            </div>
          </div>
          <div className="animate-in fade-in rounded-lg border border-border bg-card p-5 shadow-sm duration-500 lg:col-span-2" style={{ animationDelay: "300ms" }}>
            <div className="mb-4 flex items-center gap-2">
              <Skeleton className="size-4 rounded" />
              <Skeleton className="h-4 w-16" />
            </div>
            <div className="flex items-end gap-2 px-4">
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="flex flex-1 items-end gap-0.5">
                  <Skeleton className="flex-1 rounded-t" style={{ height: `${60 + Math.random() * 120}px` }} />
                  <Skeleton className="flex-1 rounded-t" style={{ height: `${20 + Math.random() * 40}px` }} />
                </div>
              ))}
            </div>
          </div>
        </div>
      )
    }

    return (
      <div className="grid gap-4 lg:grid-cols-5">
        {/* Area chart - trend */}
        <div className="animate-in fade-in slide-in-from-bottom-1 rounded-lg border border-border bg-card p-5 shadow-sm transition-shadow duration-300 hover:shadow-md duration-500 lg:col-span-3">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <TrendingUp className="size-4 text-muted-foreground" />
              <h3 className="text-sm font-medium text-foreground">24h 健康趋势</h3>
            </div>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full bg-primary" />
                正常
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full bg-warning" />
                警告
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full bg-negative" />
                严重
              </span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={MOCK_TREND_DATA} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
              <defs>
                <linearGradient id="gradientHealthy" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={ACCENT_STYLES.primary.cssColor} stopOpacity={0.3} />
                  <stop offset="50%" stopColor={ACCENT_STYLES.primary.cssColor} stopOpacity={0.08} />
                  <stop offset="100%" stopColor={ACCENT_STYLES.primary.cssColor} stopOpacity={0.01} />
                </linearGradient>
                <linearGradient id="gradientWarning" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={ACCENT_STYLES.warning.cssColor} stopOpacity={0.2} />
                  <stop offset="100%" stopColor={ACCENT_STYLES.warning.cssColor} stopOpacity={0.01} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-chart-grid)" strokeOpacity={0.5} />
              <XAxis dataKey="hour" tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} tickLine={false} axisLine={false} interval={3} />
              <YAxis tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} tickLine={false} axisLine={false} />
              <Tooltip content={<CustomTooltip />} />
              <Area type="monotone" dataKey="healthy" name="正常" stroke={ACCENT_STYLES.primary.cssColor} strokeWidth={2} fill="url(#gradientHealthy)" dot={false} activeDot={{ r: 4, strokeWidth: 2, fill: "var(--color-surface)" }} />
              <Area type="monotone" dataKey="warning" name="警告" stroke={ACCENT_STYLES.warning.cssColor} strokeWidth={1.5} fill="url(#gradientWarning)" dot={false} activeDot={{ r: 3, strokeWidth: 2, fill: "var(--color-surface)" }} />
              <Area type="monotone" dataKey="critical" name="严重" stroke={ACCENT_STYLES.negative.cssColor} strokeWidth={1.5} fill="transparent" dot={false} activeDot={{ r: 3, strokeWidth: 2, fill: "var(--color-surface)" }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Bar chart - distribution */}
        <div className="animate-in fade-in slide-in-from-bottom-1 rounded-lg border border-border bg-card p-5 shadow-sm transition-shadow duration-300 hover:shadow-md duration-500 lg:col-span-2" style={{ animationDelay: "100ms" }}>
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BarChart3 className="size-4 text-muted-foreground" />
              <h3 className="text-sm font-medium text-foreground">周度趋势</h3>
            </div>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-sm bg-primary" />
                健康
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-sm bg-negative" />
                风险
              </span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={MOCK_DISTRIBUTION_DATA} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-chart-grid)" strokeOpacity={0.5} vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} tickLine={false} axisLine={false} />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: "var(--color-primary-muted)", opacity: 0.3 }} />
              <Bar dataKey="healthy" name="健康" fill={ACCENT_STYLES.primary.cssColor} radius={[3, 3, 0, 0]} maxBarSize={28} />
              <Bar dataKey="risk" name="风险" fill={ACCENT_STYLES.negative.cssColor} radius={[3, 3, 0, 0]} maxBarSize={28} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    )
  }

  // ── Table ───────────────────────────────────────────────────────────────────
  function renderTable() {
    return (
      <section className="animate-in fade-in slide-in-from-bottom-1 duration-500" style={{ animationDelay: "200ms" }}>
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as ActiveTab)} className="w-fit">
              <TabsList>
                <TabsTrigger value={TAB_OVERVIEW}>全部对象</TabsTrigger>
                <TabsTrigger value={TAB_RISK}>风险对象</TabsTrigger>
              </TabsList>
            </Tabs>
            <span className="text-xs tabular-nums text-muted-foreground">
              {filteredIssues.length} 项结果
            </span>
          </div>
          {activeTab === TAB_RISK ? (
            <div className="flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-2.5 py-1 text-xs text-muted-foreground">
              <ShieldAlert className="size-3" />
              仅显示严重和警告
            </div>
          ) : null}
        </div>

        <ListTable>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[280px]">对象</TableHead>
                <TableHead className="w-28">严重程度</TableHead>
                <TableHead className="w-32">类型</TableHead>
                <TableHead>问题摘要</TableHead>
                <TableHead className="w-24">风险分</TableHead>
                <TableHead className="w-36">更新时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <ListTableLoadingRows rowCount={6} columnCount={7} />
              ) : pagedIssues.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="h-32 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <Filter className="size-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">没有匹配的结果</p>
                      {searchQuery ? (
                        <Button variant="ghost" size="sm" onClick={() => setSearchQuery("")}>
                          清除搜索
                        </Button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                pagedIssues.map((issue, index) => (
                  <TableRow
                    key={issue.id}
                    className={cn(
                      "cursor-pointer transition-colors duration-150 hover:bg-muted/40",
                      selectedIssue?.id === issue.id && drawerOpen && "bg-primary/[0.04]"
                    )}
                    style={{ animationDelay: `${index * 30}ms` }}
                    onClick={() => openDetail(issue)}
                  >
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                          <Server className="size-3.5 text-muted-foreground" />
                        </div>
                        <span className="font-medium text-foreground">{issue.object}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <span className={cn("inline-block size-1.5 rounded-full", SEVERITY_CONFIG[issue.severity].dotBg)} />
                        <Badge variant={SEVERITY_CONFIG[issue.severity].variant} className="text-[11px]">
                          {SEVERITY_CONFIG[issue.severity].label}
                        </Badge>
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{issue.kind}</TableCell>
                    <TableCell
                      className="max-w-[400px] truncate text-muted-foreground"
                      title={issue.summary}
                    >
                      {issue.summary}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-14 overflow-hidden rounded-full bg-muted/60">
                          <div
                            className={cn(
                              "h-full rounded-full transition-all duration-500",
                              issue.score > 70 ? "bg-negative" : issue.score > 40 ? "bg-warning" : "bg-positive"
                            )}
                            style={{ width: `${issue.score}%` }}
                          />
                        </div>
                        <span className="text-xs tabular-nums text-muted-foreground">{issue.score}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground tabular-nums">{issue.updatedAt}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={(e) => {
                          e.stopPropagation()
                          openDetail(issue)
                        }}
                      >
                        <ChevronRight className="size-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
          {!loading ? (
            <PaginationFooter
              page={page}
              pageSize={PAGE_SIZE}
              total={filteredIssues.length}
              onPageChange={setPage}
              className="border-t border-border px-4 py-2"
            />
          ) : null}
        </ListTable>
      </section>
    )
  }

  // ── Drawer ──────────────────────────────────────────────────────────────────
  function renderDrawerInfoContent() {
    if (!drawerDetail || !selectedIssue) return null
    return (
      <div className="space-y-5 overflow-y-auto px-5 py-4">
        {/* Detail Sections */}
        {drawerDetail.sections.map((section) => (
          <section key={section.key} className="rounded-lg border border-border bg-muted/10 p-4">
            <h4 className="mb-3 text-sm font-medium text-foreground">{section.title}</h4>
            <div className="grid gap-3 sm:grid-cols-2">
              {section.fields.map((field) => (
                <div key={field.label}>
                  <p className="text-[11px] uppercase tracking-wider text-muted-foreground">{field.label}</p>
                  <p className="mt-0.5 text-sm text-foreground">{field.value}</p>
                </div>
              ))}
            </div>
          </section>
        ))}

        {/* Timeline */}
        <section className="rounded-lg border border-border p-4">
          <h4 className="mb-4 text-sm font-medium text-foreground">执行时间线</h4>
          <div className="relative ml-2">
            {drawerDetail.timeline.map((entry, i) => (
              <div key={i} className="group relative flex gap-4 pb-5 last:pb-0">
                {i < drawerDetail.timeline.length - 1 ? (
                  <div className="absolute left-[5px] top-3 h-full w-px bg-border" />
                ) : null}
                <div className="relative z-10 mt-0.5 flex shrink-0 items-center justify-center">
                  <div className={cn(
                    "size-[11px] rounded-full border-2",
                    entry.status === "success"
                      ? "border-positive bg-positive/20"
                      : "border-negative bg-negative/20"
                  )} />
                </div>
                <div className="flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-xs font-medium tabular-nums text-foreground">{entry.time}</span>
                    <Badge
                      variant={entry.status === "success" ? "secondary" : "destructive"}
                      className="px-1.5 py-0 text-[10px]"
                    >
                      {entry.status === "success" ? "成功" : "失败"}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{entry.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* SQL Display - dark code block style */}
        <section className="rounded-lg border border-border">
          <div className="flex items-center justify-between border-b border-[#2a2a3a] bg-[#1e1e2e] px-4 py-2.5 rounded-t-lg">
            <div className="flex items-center gap-2">
              <Code2 className="size-3.5 text-[#a78bfa]" />
              <span className="text-xs font-medium text-[#cdd6f4]">最近收集 SQL</span>
            </div>
            <button
              type="button"
              className="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] text-[#a6adc8] transition-colors hover:bg-[#313244] hover:text-[#cdd6f4]"
            >
              <ClipboardCopy className="size-3" />
              复制
            </button>
          </div>
          <div className="overflow-x-auto bg-[#1e1e2e] p-4 rounded-b-lg">
            <pre className="text-[13px] leading-relaxed font-mono">
              <code>
                <span className="text-[#cba6f7]">{"ANALYZE"}</span>
                <span className="text-[#cdd6f4]">{" TABLE "}</span>
                <span className="text-[#89b4fa]">{selectedIssue?.object || "db_1.t_order_001"}</span>
                <span className="text-[#cdd6f4]">{";"}</span>
                {"\n\n"}
                <span className="text-[#6c7086]">{"-- 最后一次成功执行的收集语句"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"SELECT"}</span>
                <span className="text-[#cdd6f4]">{" /*+ "}</span>
                <span className="text-[#a6e3a1]">{"QUERY_TIMEOUT(30000000)"}</span>
                <span className="text-[#cdd6f4]">{" */ "}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  table_name"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                <span className="text-[#cdd6f4]">{" partition_name"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  last_analyzed"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                <span className="text-[#cdd6f4]">{" num_rows"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  avg_row_len"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                <span className="text-[#cdd6f4]">{" stale_percent"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"FROM"}</span>
                <span className="text-[#cdd6f4]">{" oceanbase"}</span>
                <span className="text-[#a6adc8]">{"."}</span>
                <span className="text-[#89b4fa]">{"__all_table_stat"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"WHERE"}</span>
                <span className="text-[#cdd6f4]">{" tenant_id "}</span>
                <span className="text-[#89dceb]">{"="}</span>
                <span className="text-[#fab387]">{" 1002"}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  "}</span>
                <span className="text-[#cba6f7]">{"AND"}</span>
                <span className="text-[#cdd6f4]">{" table_name "}</span>
                <span className="text-[#89dceb]">{"="}</span>
                <span className="text-[#a6e3a1]">{` '${selectedIssue?.object.split(".")[1] || "t_order_001"}'`}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"ORDER BY"}</span>
                <span className="text-[#cdd6f4]">{" last_analyzed "}</span>
                <span className="text-[#cba6f7]">{"DESC"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"LIMIT"}</span>
                <span className="text-[#fab387]">{" 20"}</span>
                <span className="text-[#cdd6f4]">{";"}</span>
              </code>
            </pre>
          </div>
        </section>

        {/* Schema Display - dark code block style */}
        <section className="rounded-lg border border-border">
          <div className="flex items-center justify-between border-b border-[#2a2a3a] bg-[#1e1e2e] px-4 py-2.5 rounded-t-lg">
            <div className="flex items-center gap-2">
              <Table2 className="size-3.5 text-[#89b4fa]" />
              <span className="text-xs font-medium text-[#cdd6f4]">表结构</span>
              <span className="rounded-md bg-[#313244] px-1.5 py-0.5 text-[10px] text-[#a6adc8]">8 列</span>
            </div>
            <button
              type="button"
              className="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] text-[#a6adc8] transition-colors hover:bg-[#313244] hover:text-[#cdd6f4]"
            >
              <ClipboardCopy className="size-3" />
              复制
            </button>
          </div>
          <div className="overflow-x-auto bg-[#1e1e2e] p-4 rounded-b-lg">
            <pre className="text-[13px] leading-relaxed font-mono">
              <code>
                <span className="text-[#cba6f7]">{"CREATE TABLE"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#89b4fa]">{selectedIssue?.object.split(".")[1] || "t_order_001"}</span>
                <span className="text-[#cdd6f4]">{" ("}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  id"}</span>
                <span className="text-[#cdd6f4]">{"            "}</span>
                <span className="text-[#f9e2af]">{"BIGINT"}</span>
                <span className="text-[#cdd6f4]">{"       "}</span>
                <span className="text-[#cba6f7]">{"NOT NULL"}</span>
                <span className="text-[#cdd6f4]">{"  "}</span>
                <span className="text-[#cba6f7]">{"AUTO_INCREMENT"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  order_no"}</span>
                <span className="text-[#cdd6f4]">{"       "}</span>
                <span className="text-[#f9e2af]">{"VARCHAR(64)"}</span>
                <span className="text-[#cdd6f4]">{"  "}</span>
                <span className="text-[#cba6f7]">{"NOT NULL"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                <span className="text-[#cdd6f4]">{"   "}</span>
                <span className="text-[#6c7086]">{"-- 订单号"}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  user_id"}</span>
                <span className="text-[#cdd6f4]">{"        "}</span>
                <span className="text-[#f9e2af]">{"BIGINT"}</span>
                <span className="text-[#cdd6f4]">{"       "}</span>
                <span className="text-[#cba6f7]">{"NOT NULL"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  amount"}</span>
                <span className="text-[#cdd6f4]">{"         "}</span>
                <span className="text-[#f9e2af]">{"DECIMAL(12,2)"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#cba6f7]">{"DEFAULT"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#fab387]">{"0.00"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  status"}</span>
                <span className="text-[#cdd6f4]">{"         "}</span>
                <span className="text-[#f9e2af]">{"TINYINT"}</span>
                <span className="text-[#cdd6f4]">{"      "}</span>
                <span className="text-[#cba6f7]">{"DEFAULT"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#fab387]">{"0"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                <span className="text-[#cdd6f4]">{"       "}</span>
                <span className="text-[#6c7086]">{"-- 0:待付款 1:已付款 2:已取消"}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  tenant_id"}</span>
                <span className="text-[#cdd6f4]">{"      "}</span>
                <span className="text-[#f9e2af]">{"INT"}</span>
                <span className="text-[#cdd6f4]">{"          "}</span>
                <span className="text-[#cba6f7]">{"NOT NULL"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  created_at"}</span>
                <span className="text-[#cdd6f4]">{"     "}</span>
                <span className="text-[#f9e2af]">{"DATETIME"}</span>
                <span className="text-[#cdd6f4]">{"     "}</span>
                <span className="text-[#cba6f7]">{"DEFAULT"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#cba6f7]">{"CURRENT_TIMESTAMP"}</span>
                <span className="text-[#a6adc8]">{","}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  updated_at"}</span>
                <span className="text-[#cdd6f4]">{"     "}</span>
                <span className="text-[#f9e2af]">{"DATETIME"}</span>
                <span className="text-[#cdd6f4]">{"     "}</span>
                <span className="text-[#cba6f7]">{"DEFAULT"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#cba6f7]">{"CURRENT_TIMESTAMP"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#cba6f7]">{"ON UPDATE"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#cba6f7]">{"CURRENT_TIMESTAMP"}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{")"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#cba6f7]">{"ENGINE"}</span>
                <span className="text-[#89dceb]">{"="}</span>
                <span className="text-[#a6e3a1]">{"InnoDB"}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  "}</span>
                <span className="text-[#cba6f7]">{"DEFAULT CHARSET"}</span>
                <span className="text-[#89dceb]">{"="}</span>
                <span className="text-[#a6e3a1]">{"utf8mb4"}</span>
                {"\n"}
                <span className="text-[#cdd6f4]">{"  "}</span>
                <span className="text-[#cba6f7]">{"COMMENT"}</span>
                <span className="text-[#89dceb]">{"="}</span>
                <span className="text-[#a6e3a1]">{"'订单主表'"}</span>
                <span className="text-[#cdd6f4]">{";"}</span>
                {"\n\n"}
                <span className="text-[#6c7086]">{"-- Indexes"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"PRIMARY KEY"}</span>
                <span className="text-[#cdd6f4]">{" ("}</span>
                <span className="text-[#89b4fa]">{"id"}</span>
                <span className="text-[#cdd6f4]">{")"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"UNIQUE KEY"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#a6e3a1]">{"uk_order_no"}</span>
                <span className="text-[#cdd6f4]">{" ("}</span>
                <span className="text-[#89b4fa]">{"order_no"}</span>
                <span className="text-[#cdd6f4]">{")"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"KEY"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#a6e3a1]">{"idx_user_status"}</span>
                <span className="text-[#cdd6f4]">{" ("}</span>
                <span className="text-[#89b4fa]">{"user_id"}</span>
                <span className="text-[#a6adc8]">{", "}</span>
                <span className="text-[#89b4fa]">{"status"}</span>
                <span className="text-[#cdd6f4]">{")"}</span>
                {"\n"}
                <span className="text-[#cba6f7]">{"KEY"}</span>
                <span className="text-[#cdd6f4]">{" "}</span>
                <span className="text-[#a6e3a1]">{"idx_tenant_created"}</span>
                <span className="text-[#cdd6f4]">{" ("}</span>
                <span className="text-[#89b4fa]">{"tenant_id"}</span>
                <span className="text-[#a6adc8]">{", "}</span>
                <span className="text-[#89b4fa]">{"created_at"}</span>
                <span className="text-[#cdd6f4]">{")"}</span>
              </code>
            </pre>
          </div>
        </section>
      </div>
    )
  }

  function renderDrawerChatContent() {
    if (!selectedIssue) return null

    const mockSkills = ["ob-stats-ops", "execute_sql", "explain_sql"]

    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {/* Skills loaded bar */}
        <div className="flex items-center gap-2 border-b border-border px-5 py-2">
          <Sparkles className="size-3 text-primary" />
          <span className="text-[11px] text-muted-foreground">已加载</span>
          {mockSkills.map((skill) => (
            <span
              key={skill}
              className="inline-flex items-center rounded-full border border-border bg-muted/30 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
            >
              {skill}
            </span>
          ))}
        </div>

        {/* Chat messages area */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <div className="space-y-4">
            {/* Empty state with suggestion chips - hidden once conversation starts */}
            {false && (
              <div className="flex h-full flex-col items-center justify-center gap-4 py-12">
                <div className="flex size-10 items-center justify-center rounded-full bg-muted">
                  <MessageSquare className="size-5 text-muted-foreground" />
                </div>
                <p className="text-sm text-muted-foreground">针对该对象发起 AI 分析</p>
                <div className="grid w-full max-w-sm grid-cols-2 gap-2">
                  {[
                    "直方图分布是否偏斜？",
                    "列基数估算偏差多少？",
                    "给出低风险执行步骤",
                    "是否需要手动收集？",
                  ].map((s) => (
                    <button
                      key={s}
                      type="button"
                      className="rounded-lg border border-border bg-card px-3 py-2 text-left text-xs text-foreground transition-colors hover:bg-muted"
                      onClick={() => setMockChatInput(s)}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* User message */}
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-xl bg-primary px-3 py-2 text-sm text-primary-foreground">
                请对 {selectedIssue.object} 做统计信息深度诊断，先给结论再给证据。
              </div>
            </div>

            {/* Tool call - completed */}
            <div className="flex justify-start">
              <div className="w-full max-w-[90%] space-y-2">
                <div className="rounded-lg border border-border bg-card">
                  <div className="flex items-center gap-2 px-3 py-2">
                    <CheckCircle2 className="size-4 shrink-0 text-positive" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">
                        工具调用：execute_sql
                      </p>
                      <p className="text-xs text-muted-foreground">
                        执行成功，返回 12 条记录
                      </p>
                    </div>
                    <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/60">1.2s</span>
                    <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
                  </div>
                </div>
              </div>
            </div>

            {/* Assistant message */}
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground">
                <p className="mb-2 font-medium">结论：该表统计信息严重过期，存在执行计划退化风险。</p>
                <p className="text-muted-foreground">
                  基于最近 12 条收集记录分析，最近 3 次收集均以错误码 4013（锁冲突）失败。
                  最后一次成功收集距今已超 72 小时。DML 变更量在最近 24h 内超过阈值 15%。
                </p>
                <p className="mt-2 text-muted-foreground">
                  接下来我会查询该表的直方图分布和列基数信息，以评估执行计划偏差程度。
                </p>
              </div>
            </div>

            {/* Tool call - running */}
            <div className="flex justify-start">
              <div className="w-full max-w-[90%]">
                <div className="rounded-lg border border-border bg-card">
                  <div className="flex items-center gap-2 px-3 py-2">
                    <Loader2 className="size-4 shrink-0 animate-spin text-primary" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">
                        工具调用：explain_sql
                      </p>
                      <p className="text-xs text-muted-foreground">执行中</p>
                    </div>
                    <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
                  </div>
                </div>
              </div>
            </div>

            {/* Pending authorization card */}
            <div className="flex justify-start">
              <div className="w-full max-w-[90%]">
                <div className="rounded-lg border border-primary/30 bg-primary/[0.03] p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <ShieldAlert className="size-4 text-primary" />
                    <span className="text-sm font-medium text-foreground">需要确认执行</span>
                  </div>
                  <div className="mb-3 space-y-1 text-xs text-muted-foreground">
                    <p>cn-hangzhou-prod · user · datasource#3</p>
                    <p>tenant: ob_tenant=tenant_2</p>
                  </div>
                  <div className="rounded-lg border border-border bg-muted/30 p-2">
                    <pre className="text-xs text-foreground">
{`SELECT /*+ QUERY_TIMEOUT(30000000) */
  table_name, partition_name,
  last_analyzed, num_rows, stale_percent
FROM oceanbase.__all_table_stat
WHERE tenant_id = 1002
  AND table_name = '${selectedIssue.object.split(".")[1] || "t_order"}'
ORDER BY last_analyzed DESC
LIMIT 20;`}
                    </pre>
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <Button size="xs">确认执行</Button>
                    <Button size="xs" variant="outline">取消</Button>
                    <span className="ml-auto text-[10px] text-muted-foreground">指纹: a3f8c1d2</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Thinking indicator */}
            <div className="flex justify-start">
              <div className="rounded-xl border border-border bg-muted px-3 py-2">
                <div className="inline-flex items-center gap-1">
                  {[0, 1, 2].map((idx) => (
                    <span
                      key={idx}
                      className="size-1.5 animate-pulse rounded-full bg-muted-foreground/60"
                      style={{ animationDelay: `${idx * 150}ms`, animationDuration: "1.2s" }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Input area */}
        <div className="border-t border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Input
              value={mockChatInput}
              onChange={(e) => setMockChatInput(e.target.value)}
              placeholder="输入诊断问题..."
              className="text-sm"
            />
            <Button
              variant={mockChatInput.trim() ? "default" : "outline"}
              size="icon"
              disabled={!mockChatInput.trim()}
            >
              <Send className="size-4" />
            </Button>
          </div>
        </div>
      </div>
    )
  }

  function renderDrawer() {
    const title = drawerDetail?.title || selectedIssue?.object || "对象详情"

    return (
      <Drawer open={drawerOpen} onOpenChange={(open) => {
        setDrawerOpen(open)
        if (!open) setDrawerMode("info")
      }}>
        <DrawerContent className="flex max-w-[780px] flex-col" showCloseButton={false} aria-describedby={undefined}>
          <DrawerHeader className="shrink-0 border-b border-border px-5 py-3">
            <div className="flex items-center justify-between">
              <DrawerTitle className="truncate text-sm font-semibold">{title}</DrawerTitle>
              <div className="flex items-center gap-2">
                <Tabs
                  value={drawerMode}
                  onValueChange={(v) => setDrawerMode(v as "info" | "chat")}
                >
                  <TabsList>
                    <TabsTrigger value="info">详情</TabsTrigger>
                    <TabsTrigger value="chat">AI 分析</TabsTrigger>
                  </TabsList>
                </Tabs>
                <DrawerClose className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground" aria-label="关闭">
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                </DrawerClose>
              </div>
            </div>
          </DrawerHeader>
          <div className="flex min-h-0 flex-1 flex-col">
            {drawerMode === "info" ? renderDrawerInfoContent() : renderDrawerChatContent()}
          </div>
        </DrawerContent>
      </Drawer>
    )
  }

  // ── Primary Content ─────────────────────────────────────────────────────────
  const primary = (
    <div className="space-y-8">
      {renderStatCards()}
      {renderCharts()}
      {renderTable()}
    </div>
  )

  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />
      {renderDrawer()}
    </>
  )
}
