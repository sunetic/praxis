import { useEffect, useMemo, useState } from "react"
import { ArrowLeft, CalendarCheck, Check, ChevronRight, Code2, Copy, Database, Loader2, RefreshCw, ShieldAlert } from "lucide-react"
import { toast } from "sonner"

import { ClusterScopeSelector } from "@/components/stats-analysis/ClusterScopeSelector"
import { StatsOverviewCards } from "@/components/stats-analysis/StatsOverviewCards"
import { ALL_CLUSTERS, useStatsAnalysisWorkbench } from "@/components/stats-analysis/useStatsAnalysisWorkbench"
import { SceneAgentChatShell } from "@/components/shared/PageAgentChatShell"
import type { SceneBusinessAgentAdapter } from "@/components/shared/pageAgentAdapter"
import type {
  StatsCollectionDaySummary,
  StatsDailyFailedTableItem,
  StatsDailyTaskItem,
  StatsDrawerDetailResponse,
  StatsIssueItem,
  StatsRiskCandidateItem,
  StatsTenantConfigCheck,
} from "@/lib/api"
import { statsAnalysisApi } from "@/lib/api"
import { cn } from "@/lib/utils"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Drawer, DrawerBody, DrawerClose, DrawerContent, DrawerHeader, DrawerTitle } from "@/components/ui/drawer"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

type DrawerMode = "info" | "chat"
type DrawerView = "day_overview" | "table_detail" | "tenant_config"
const TOP_TAB_COLLECTION = "collection"
const TOP_TAB_RISK = "risk"
type TopTab = typeof TOP_TAB_COLLECTION | typeof TOP_TAB_RISK
type RiskSubTab = "active" | "history"
const TABLE_PAGE_SIZE = 10
const DRAWER_TASK_PAGE_SIZE = 10

function readErrorMessage(error: unknown, fallback: string) {
  if (error instanceof Error && error.message.trim().length > 0) return error.message.trim()
  if (typeof error === "string" && error.trim().length > 0) return error.trim()
  return fallback
}

function fmtDuration(minutes: number): string {
  if (minutes < 1) return `${Math.round(minutes * 60)}s`
  if (minutes < 60) return `${Math.round(minutes)}m`
  return `${Math.floor(minutes / 60)}h ${Math.round(minutes % 60)}m`
}

function fmtTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "-"
  const d = new Date(isoStr)
  if (Number.isNaN(d.getTime())) return isoStr
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"] as const

function fmtDateWithWeekday(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00")
  if (Number.isNaN(d.getTime())) return dateStr
  return `${dateStr.slice(5)} ${WEEKDAYS[d.getDay()]}`
}

function getDatasourceMeta(datasources: Array<{ id: number; name: string; cluster_key: string }>, datasourceId?: number | null) {
  if (!datasourceId) return null
  return datasources.find((item) => item.id === datasourceId) ?? null
}

const severityConfig: Record<string, { label: string; variant: "destructive" | "secondary" | "outline"; dotBg: string }> = {
  high: { label: "高", variant: "destructive", dotBg: "bg-negative" },
  medium: { label: "中", variant: "outline", dotBg: "bg-warning" },
  low: { label: "低", variant: "secondary", dotBg: "bg-positive" },
}

export function StatsAnalysisPage() {
  const {
    loadingDatasources,
    loadingWorkbench: loadingOverview,
    scopedDatasources,
    clusterOptions,
    selectedClusterKey,
    selectedDatasourceId,
    selectedIssueId,
    selectedIssue,
    workbench: overviewData,
    setSelectedClusterKey,
    setSelectedDatasourceId,
    setSelectedIssueId,
    refreshWorkbench: refreshOverview,
  } = useStatsAnalysisWorkbench()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerMode, setDrawerMode] = useState<DrawerMode>("info")
  const [drawerView, setDrawerView] = useState<DrawerView>("day_overview")
  const [topTab, setTopTab] = useState<TopTab>(TOP_TAB_COLLECTION)

  // -- Collection tab --
  const [dailySummaries, setDailySummaries] = useState<StatsCollectionDaySummary[]>([])
  const [loadingDailySummaries, setLoadingDailySummaries] = useState(false)
  const [dailySummaryPage, setDailySummaryPage] = useState(1)

  // -- Drawer: day overview drill-in --
  const [drawerDayDate, setDrawerDayDate] = useState<string | null>(null)
  const [dailyFailedTables, setDailyFailedTables] = useState<StatsDailyFailedTableItem[]>([])
  const [loadingDailyFailedTables, setLoadingDailyFailedTables] = useState(false)
  const [dailyTasks, setDailyTasks] = useState<StatsDailyTaskItem[]>([])
  const [dailyTasksTotal, setDailyTasksTotal] = useState(0)
  const [loadingDailyTasks, setLoadingDailyTasks] = useState(false)
  const [dailyTaskPage, setDailyTaskPage] = useState(1)
  const [dailyTaskFilterType, setDailyTaskFilterType] = useState<string | null>(null)
  const [dailyTaskFilterStatus, setDailyTaskFilterStatus] = useState<string | null>(null)

  // -- Risk tab: active + history --
  const [riskCandidates, setRiskCandidates] = useState<StatsRiskCandidateItem[]>([])
  const [loadingRiskCandidates, setLoadingRiskCandidates] = useState(false)
  const [riskSubTab, setRiskSubTab] = useState<RiskSubTab>("active")
  const [historyRiskCandidates, setHistoryRiskCandidates] = useState<StatsRiskCandidateItem[]>([])
  const [loadingHistoryRisk, setLoadingHistoryRisk] = useState(false)
  const [selectedRiskCandidateId, setSelectedRiskCandidateId] = useState<number | null>(null)
  const [riskPage, setRiskPage] = useState(1)
  const [historyRiskPage, setHistoryRiskPage] = useState(1)

  // -- Drawer --
  const [syntheticIssue, setSyntheticIssue] = useState<StatsIssueItem | null>(null)
  const [drawerDatasourceId, setDrawerDatasourceId] = useState<number | null>(null)
  const [drawerDetail, setDrawerDetail] = useState<StatsDrawerDetailResponse | null>(null)
  const [drawerDetailLoading, setDrawerDetailLoading] = useState(false)
  const [drawerDetailError, setDrawerDetailError] = useState<string | null>(null)
  const [chatSuggestedPrompt, setChatSuggestedPrompt] = useState<string | null>(null)
  const [selectedTenantConfig, setSelectedTenantConfig] = useState<StatsTenantConfigCheck | null>(null)
  const [tenantSqlCopied, setTenantSqlCopied] = useState(false)

  // effectiveIssue: prefer hook's selectedIssue, fall back to synthetic issue from daily failed table
  const effectiveIssue = selectedIssue ?? syntheticIssue
  const effectiveDatasourceId = drawerDatasourceId ?? effectiveIssue?.datasource_id ?? selectedDatasourceId

  const selectedRiskCandidate = useMemo(
    () => {
      const all = riskSubTab === "active" ? riskCandidates : historyRiskCandidates
      return all.find((item) => item.candidate_id === selectedRiskCandidateId) ?? null
    },
    [riskCandidates, historyRiskCandidates, riskSubTab, selectedRiskCandidateId]
  )

  const isAllMode = !selectedDatasourceId
  const effectiveClusterKey = selectedClusterKey === ALL_CLUSTERS ? undefined : selectedClusterKey
  const drawerScopedDatasourceId = drawerDatasourceId ?? selectedDatasourceId

  const selectedDatasource = useMemo(
    () => scopedDatasources.find((item) => item.id === effectiveDatasourceId) ?? null,
    [scopedDatasources, effectiveDatasourceId]
  )

  const dailySummaryDatasources = useMemo(() => {
    const ids = Array.from(new Set(dailySummaries.map((item) => item.datasource_id).filter((item): item is number => !!item)))
    return new Map(ids.map((id) => [id, getDatasourceMeta(scopedDatasources, id)]))
  }, [dailySummaries, scopedDatasources])

  const dailyTaskDatasources = useMemo(() => {
    const ids = Array.from(new Set(dailyTasks.map((item) => item.datasource_id).filter((item): item is number => !!item)))
    return new Map(ids.map((id) => [id, getDatasourceMeta(scopedDatasources, id)]))
  }, [dailyTasks, scopedDatasources])

  const failedTableDatasources = useMemo(() => {
    const ids = Array.from(new Set(dailyFailedTables.map((item) => item.datasource_id).filter((item): item is number => !!item)))
    return new Map(ids.map((id) => [id, getDatasourceMeta(scopedDatasources, id)]))
  }, [dailyFailedTables, scopedDatasources])

  // ---- Data loading: daily collection summaries ----
  useEffect(() => {
    if (!selectedDatasourceId && !scopedDatasources.length) return
    let cancelled = false
    setLoadingDailySummaries(true)
    statsAnalysisApi
      .getDailyCollectionSummary({
        datasource_id: selectedDatasourceId ?? undefined,
        cluster_key: effectiveClusterKey,
        lookback_days: 7,
      })
      .then((payload) => {
        if (cancelled) return
        setDailySummaries(payload.items)
        setDailySummaryPage(1)
      })
      .catch(() => {
        if (!cancelled) setDailySummaries([])
      })
      .finally(() => {
        if (!cancelled) setLoadingDailySummaries(false)
      })
    return () => { cancelled = true }
  }, [selectedDatasourceId, selectedClusterKey, scopedDatasources.length])

  // ---- Data loading: failed tables for Drawer day overview ----
  useEffect(() => {
    if (!drawerDayDate || (!selectedDatasourceId && !scopedDatasources.length)) {
      setDailyFailedTables([])
      return
    }
    let cancelled = false
    setLoadingDailyFailedTables(true)
    statsAnalysisApi
      .getDailyFailedTables({
        datasource_id: selectedDatasourceId ?? undefined,
        cluster_key: effectiveClusterKey,
        date: drawerDayDate,
      })
      .then((payload) => {
        if (cancelled) return
        setDailyFailedTables(payload.items)
      })
      .catch(() => {
        if (!cancelled) setDailyFailedTables([])
      })
      .finally(() => {
        if (!cancelled) setLoadingDailyFailedTables(false)
      })
    return () => { cancelled = true }
  }, [selectedDatasourceId, selectedClusterKey, scopedDatasources.length, drawerDayDate])

  // ---- Data loading: tasks for Drawer day overview (server-side pagination + filtering) ----
  useEffect(() => {
    if (!drawerDayDate || (!selectedDatasourceId && !scopedDatasources.length)) {
      setDailyTasks([])
      setDailyTasksTotal(0)
      return
    }
    let cancelled = false
    setLoadingDailyTasks(true)
    statsAnalysisApi
      .getDailyTasks({
        datasource_id: selectedDatasourceId ?? undefined,
        cluster_key: effectiveClusterKey,
        date: drawerDayDate,
        page: dailyTaskPage,
        page_size: DRAWER_TASK_PAGE_SIZE,
        task_type: dailyTaskFilterType,
        status: dailyTaskFilterStatus,
      })
      .then((payload) => {
        if (cancelled) return
        setDailyTasks(payload.items)
        setDailyTasksTotal(payload.total)
      })
      .catch(() => {
        if (!cancelled) {
          setDailyTasks([])
          setDailyTasksTotal(0)
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingDailyTasks(false)
      })
    return () => { cancelled = true }
  }, [selectedDatasourceId, selectedClusterKey, scopedDatasources.length, drawerDayDate, dailyTaskPage, dailyTaskFilterType, dailyTaskFilterStatus])

  // ---- Data loading: active risk candidates ----
  useEffect(() => {
    if (!selectedDatasourceId) return
    let cancelled = false
    setLoadingRiskCandidates(true)
    statsAnalysisApi
      .listRiskCandidates({ datasource_id: selectedDatasourceId, lifecycle_status: "active" })
      .then((payload) => {
        if (cancelled) return
        setRiskCandidates(payload.items)
        setSelectedRiskCandidateId((currentId) => {
          if (!currentId) return payload.items[0]?.candidate_id ?? null
          return payload.items.some((item) => item.candidate_id === currentId) ? currentId : (payload.items[0]?.candidate_id ?? null)
        })
      })
      .catch((error) => {
        if (cancelled) return
        setRiskCandidates([])
        setSelectedRiskCandidateId(null)
        toast.error(`加载风险候选失败：${readErrorMessage(error, "请稍后重试")}`)
      })
      .finally(() => {
        if (!cancelled) setLoadingRiskCandidates(false)
      })
    return () => { cancelled = true }
  }, [selectedDatasourceId])

  // ---- Data loading: history risk candidates (lazy, on sub-tab switch) ----
  useEffect(() => {
    if (!selectedDatasourceId || topTab !== TOP_TAB_RISK || riskSubTab !== "history") return
    let cancelled = false
    setLoadingHistoryRisk(true)
    statsAnalysisApi
      .listRiskCandidates({ datasource_id: selectedDatasourceId, lifecycle_status: "resolved" })
      .then((payload) => {
        if (cancelled) return
        setHistoryRiskCandidates(payload.items)
      })
      .catch(() => {
        if (!cancelled) setHistoryRiskCandidates([])
      })
      .finally(() => {
        if (!cancelled) setLoadingHistoryRisk(false)
      })
    return () => { cancelled = true }
  }, [selectedDatasourceId, topTab, riskSubTab])

  // ---- Reset pages + drawer state on scope change ----
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRiskPage(1)
    setHistoryRiskPage(1)
    setDailySummaryPage(1)
    setDailyTaskPage(1)
    setDailyTaskFilterType(null)
    setDailyTaskFilterStatus(null)
    setSelectedTenantConfig(null)
    setSyntheticIssue(null)
    setDrawerDatasourceId(null)
    setSelectedIssueId(null)
    setSelectedRiskCandidateId(null)
    setDrawerDayDate(null)
  }, [selectedDatasourceId, selectedClusterKey])

  // ---- Drawer: close if context lost ----
  useEffect(() => {
    if (!drawerOpen) return
    if (!effectiveIssue && !selectedRiskCandidate && !selectedTenantConfig && !drawerDayDate) {
      setDrawerOpen(false)
    }
  }, [drawerOpen, effectiveIssue, selectedRiskCandidate, selectedTenantConfig, drawerDayDate])

  const hasRiskContext = !!selectedRiskCandidate

  // ---- Drawer: load detail ----
  useEffect(() => {
    if (!drawerOpen || !effectiveDatasourceId) return
    if (!effectiveIssue && !selectedRiskCandidate) return
    let cancelled = false
    setDrawerDetailLoading(true)
    setDrawerDetailError(null)
    statsAnalysisApi
      .getDrawerDetail({
        datasource_id: effectiveDatasourceId,
        issue: effectiveIssue ?? undefined,
        risk_candidate: effectiveIssue ? undefined : (selectedRiskCandidate ?? undefined),
      })
      .then((payload) => {
        if (cancelled) return
        setDrawerDetail(payload)
      })
      .catch((error) => {
        if (cancelled) return
        setDrawerDetail(null)
        setDrawerDetailError(readErrorMessage(error, "详情加载失败"))
      })
      .finally(() => {
        if (!cancelled) setDrawerDetailLoading(false)
      })
    return () => { cancelled = true }
  }, [drawerOpen, effectiveDatasourceId, effectiveIssue, selectedRiskCandidate])

  // ---- Drawer: reset mode on selection change ----
  useEffect(() => {
    if (!drawerOpen) {
      setDrawerMode("info")
      setDrawerView("day_overview")
      setChatSuggestedPrompt(null)
      setSelectedTenantConfig(null)
      return
    }
    setDrawerMode("info")
  }, [selectedIssueId, selectedRiskCandidateId, drawerOpen])

  // ---- Pagination memos ----
  const pagedDailySummaries = useMemo(() => {
    const start = (dailySummaryPage - 1) * TABLE_PAGE_SIZE
    return dailySummaries.slice(start, start + TABLE_PAGE_SIZE)
  }, [dailySummaryPage, dailySummaries])

  const currentRiskItems = riskSubTab === "active" ? riskCandidates : historyRiskCandidates
  const currentRiskPage = riskSubTab === "active" ? riskPage : historyRiskPage
  const setCurrentRiskPage = riskSubTab === "active" ? setRiskPage : setHistoryRiskPage
  const currentRiskLoading = riskSubTab === "active" ? loadingRiskCandidates : loadingHistoryRisk

  const pagedRiskCandidates = useMemo(() => {
    const start = (currentRiskPage - 1) * TABLE_PAGE_SIZE
    return currentRiskItems.slice(start, start + TABLE_PAGE_SIZE)
  }, [currentRiskPage, currentRiskItems])

  // ---- Drawer: day summary for current drawerDayDate ----
  const drawerDaySummary = useMemo(
    () => dailySummaries.find((d) => d.date === drawerDayDate) ?? null,
    [dailySummaries, drawerDayDate]
  )

  // ---- Chat adapter ----
  const statsChatFocusObject = useMemo(() => {
    if (effectiveIssue) {
      return {
        type: "issue",
        issue_id: effectiveIssue.issue_id,
        kind: effectiveIssue.kind,
        table_name: effectiveIssue.table_name,
        tenant_name: effectiveIssue.tenant_name,
        database_name: effectiveIssue.database_name,
        severity: effectiveIssue.severity,
        title: effectiveIssue.title,
        summary: effectiveIssue.summary,
        facts: effectiveIssue.facts ?? {},
      }
    }
    if (selectedRiskCandidate) {
      return {
        type: "risk_candidate",
        candidate_id: selectedRiskCandidate.candidate_id,
        table_name: selectedRiskCandidate.table_name,
        tenant_name: selectedRiskCandidate.tenant_name,
        database_name: selectedRiskCandidate.database_name,
        cluster_key: selectedRiskCandidate.cluster_key,
        severity: selectedRiskCandidate.severity,
        score: selectedRiskCandidate.score,
        latest_summary: selectedRiskCandidate.latest_summary,
        lifecycle_status: selectedRiskCandidate.lifecycle_status,
        source: selectedRiskCandidate.source,
        tags: selectedRiskCandidate.tags.map((tag) => ({
          tag_key: tag.tag_key,
          tag_label: tag.tag_label,
          severity: tag.severity,
          score: tag.score,
          summary: tag.summary,
        })),
      }
    }
    if (drawerDayDate && drawerDaySummary) {
      return {
        type: "day_overview",
        date: drawerDayDate,
        datasource_id: drawerScopedDatasourceId,
        total_tasks: drawerDaySummary.total_tasks,
        success_tables: drawerDaySummary.success_tables,
        failed_tables: drawerDaySummary.failed_tables,
        failed_tables_summary: dailyFailedTables.map((t) => ({
          owner: t.owner,
          table_name: t.table_name,
          failure_count: t.failure_count,
          latest_error: t.latest_error,
        })),
      }
    }
    return null
  }, [effectiveIssue, selectedRiskCandidate, drawerDayDate, drawerDaySummary, dailyFailedTables, drawerScopedDatasourceId])

  const statsChatAdapter = useMemo<SceneBusinessAgentAdapter>(
    () => ({
      page: "stats-analysis",
      profile: "stats_analysis_agent",
      sceneKey: "stats_analysis",
      title: "深度诊断对话",
      placeholder: "输入诊断问题，例如：为什么这张表需要直方图？",
      buildContext: () => ({
        datasource: selectedDatasource
          ? {
              id: selectedDatasource.id,
              name: selectedDatasource.name,
              cluster_key: selectedDatasource.cluster_key,
              tenant_role: selectedDatasource.tenant_role,
              host: selectedDatasource.host,
              port: selectedDatasource.port,
              db_type: selectedDatasource.db_type,
              database: selectedDatasource.database,
            }
          : null,
        selected_tab: topTab,
        drawer_mode: drawerOpen ? drawerMode : null,
        drawer_detail: drawerDetail?.chat_context ?? null,
        drawer_missing_facts: drawerDetail?.missing_facts ?? [],
      }),
    }),
    [selectedDatasource, topTab, drawerOpen, drawerMode, drawerDetail]
  )

  function handleJumpToFocusObject() {
    setDrawerMode("info")
    if (effectiveIssue) {
      setDrawerOpen(true)
      return
    }
    if (selectedRiskCandidate) {
      setTopTab(TOP_TAB_RISK)
      setDrawerOpen(true)
    }
  }

  function openDayOverviewInDrawer(day: StatsCollectionDaySummary) {
    setDrawerDayDate(day.date)
    setDrawerDatasourceId(day.datasource_id ?? null)
    setSyntheticIssue(null)
    setSelectedIssueId(null)
    setSelectedRiskCandidateId(null)
    setDrawerView("day_overview")
    setDrawerMode("info")
    setDrawerOpen(true)
  }

  function openFailedTableInDrawer(item: StatsDailyFailedTableItem) {
    const issue: StatsIssueItem = {
      issue_id: `failed:${item.datasource_id ?? 'na'}:${item.owner}.${item.table_name}:${drawerDayDate}`,
      kind: "failed_table",
      severity: (item.latest_gather_seconds ?? 0) >= 1800 ? "high" : "medium",
      title: `${item.owner}.${item.table_name} 收集失败`,
      summary: item.latest_error || "自动收集任务未成功完成",
      datasource_id: item.datasource_id ?? null,
      cluster_key: item.cluster_key ?? null,
      tenant_name: item.tenant_name || item.owner,
      database_name: null,
      table_name: item.table_name,
      facts: {
        owner: item.owner,
        error_reason: item.latest_error,
        gather_seconds: item.latest_gather_seconds,
        task_start_time: item.latest_task_start_time,
        status: item.latest_status,
      },
    }
    setSyntheticIssue(issue)
    setDrawerDatasourceId(item.datasource_id ?? null)
    setSelectedIssueId(issue.issue_id)
    setSelectedRiskCandidateId(null)
    setDrawerView("table_detail")
    setDrawerMode("info")
  }

  function openTenantConfigInDrawer(check: StatsTenantConfigCheck) {
    setSelectedTenantConfig(check)
    setSyntheticIssue(null)
    setSelectedIssueId(null)
    setSelectedRiskCandidateId(null)
    setDrawerView("tenant_config")
    setDrawerMode("info")
    setDrawerOpen(true)
  }

  function handleRefresh() {
    refreshOverview()
    if (selectedDatasourceId || scopedDatasources.length) {
      setLoadingDailySummaries(true)
      statsAnalysisApi
        .getDailyCollectionSummary({
          datasource_id: selectedDatasourceId ?? undefined,
          cluster_key: effectiveClusterKey,
          lookback_days: 7,
        })
        .then((payload) => setDailySummaries(payload.items))
        .catch(() => setDailySummaries([]))
        .finally(() => setLoadingDailySummaries(false))
    }
  }

  // ---- Toolbar ----
  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <ClusterScopeSelector
          clusterOptions={clusterOptions}
          datasources={scopedDatasources}
          clusterValue={selectedClusterKey}
          datasourceValue={selectedDatasourceId}
          disabled={loadingDatasources}
          onClusterChange={setSelectedClusterKey}
          onDatasourceChange={setSelectedDatasourceId}
        />
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button
          variant="outline"
          size="sm"
          onClick={handleRefresh}
          disabled={scopedDatasources.length === 0 || loadingOverview}
        >
          {loadingOverview ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  // ---- Collection Tab: Daily Summary (Level 1) ----
  function renderDailySummaryTable() {
    const colCount = isAllMode ? 9 : 6
    return (
      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow>
              {isAllMode ? <TableHead>集群</TableHead> : null}
              {isAllMode ? <TableHead>数据源</TableHead> : null}
              {isAllMode ? <TableHead>租户</TableHead> : null}
              <TableHead>日期</TableHead>
              <TableHead className="text-right">任务数</TableHead>
              <TableHead className="text-right">成功表</TableHead>
              <TableHead className="text-right">失败表</TableHead>
              <TableHead className="text-right">平均耗时</TableHead>
              <TableHead className="w-16 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loadingDailySummaries ? (
              <ListTableLoadingRows rowCount={6} columnCount={colCount} />
            ) : dailySummaries.length === 0 ? (
              <TableRow>
                <TableCell colSpan={colCount} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <CalendarCheck className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">近 7 天没有收集任务记录。</p>
                    <Button size="sm" variant="ghost" onClick={() => setTopTab(TOP_TAB_RISK)}>
                      查看风险检测
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              pagedDailySummaries.map((day, index) => {
                const hasFailed = day.failed_tables > 0
                const dayDatasource = dailySummaryDatasources.get(day.datasource_id ?? -1) ?? null
                return (
                  <TableRow
                    key={`${day.datasource_id ?? ""}:${day.cluster_key ?? ""}:${day.tenant_name ?? ""}:${day.date}`}
                    className={cn(
                      "cursor-pointer transition-colors duration-150 hover:bg-muted/40",
                      drawerDayDate === day.date && drawerOpen && "bg-primary/[0.04]"
                    )}
                    style={{ animationDelay: `${index * 30}ms` }}
                    onClick={() => openDayOverviewInDrawer(day)}
                  >
                    {isAllMode ? (
                      <TableCell className="text-xs text-muted-foreground">{dayDatasource?.cluster_key || day.cluster_key || "-"}</TableCell>
                    ) : null}
                    {isAllMode ? (
                      <TableCell className="text-xs text-muted-foreground">{dayDatasource?.name || (day.datasource_id ? String(day.datasource_id) : "-")}</TableCell>
                    ) : null}
                    {isAllMode ? (
                      <TableCell className="text-xs text-muted-foreground">{day.tenant_name || "-"}</TableCell>
                    ) : null}
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                          <CalendarCheck className="size-3.5 text-muted-foreground" />
                        </div>
                        <div>
                          <span className="font-medium text-foreground">{fmtDateWithWeekday(day.date)}</span>
                          <span className="ml-2 text-xs text-muted-foreground">{day.task_type}</span>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">{day.total_tasks}</TableCell>
                    <TableCell className="text-right tabular-nums text-positive">
                      {day.success_tables}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {hasFailed ? (
                        <Badge variant="destructive" className="text-[11px]">
                          {day.failed_tables}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground">0</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">{fmtDuration(day.avg_duration_min)}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="icon-xs" onClick={(e) => { e.stopPropagation(); openDayOverviewInDrawer(day) }}>
                        <ChevronRight className="size-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
        {!loadingDailySummaries && dailySummaries.length > 0 ? (
          <PaginationFooter
            page={dailySummaryPage}
            pageSize={TABLE_PAGE_SIZE}
            total={dailySummaries.length}
            onPageChange={setDailySummaryPage}
            className="border-t border-border px-4 py-2"
          />
        ) : null}
      </ListTable>
    )
  }

  // ---- Risk Tab: candidate table (shared for active + history) ----
  function renderRiskCandidatesTable() {
    const isHistory = riskSubTab === "history"
    const colCount = isHistory ? 8 : 7
    function openCandidate(candidate: StatsRiskCandidateItem) {
      setSelectedRiskCandidateId(candidate.candidate_id)
      setSelectedIssueId(null)
      setDrawerMode("info")
      setDrawerOpen(true)
    }
    return (
      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">严重程度</TableHead>
              <TableHead className="w-[280px]">对象</TableHead>
              <TableHead>摘要</TableHead>
              <TableHead className="w-32">标签</TableHead>
              <TableHead className="w-24">风险分</TableHead>
              {isHistory ? <TableHead className="w-28">状态</TableHead> : null}
              <TableHead className="w-36">最后检测</TableHead>
              <TableHead className="w-16 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {currentRiskLoading ? (
              <ListTableLoadingRows rowCount={6} columnCount={colCount} />
            ) : currentRiskItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={colCount} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <ShieldAlert className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">
                      {isHistory ? "暂无已解决的风险记录。" : "当前范围暂无活跃风险候选。"}
                    </p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              pagedRiskCandidates.map((candidate, index) => {
                const severity = candidate.severity || "low"
                const sc = severityConfig[severity] ?? severityConfig.low
                return (
                  <TableRow
                    key={candidate.candidate_id}
                    className={cn(
                      "cursor-pointer transition-colors duration-150 hover:bg-muted/40",
                      selectedRiskCandidateId === candidate.candidate_id && drawerOpen && "bg-primary/[0.04]"
                    )}
                    style={{ animationDelay: `${index * 30}ms` }}
                    onClick={() => openCandidate(candidate)}
                  >
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <span className={cn("inline-block size-1.5 rounded-full", sc.dotBg)} />
                        <Badge variant={sc.variant} className="text-[11px]">
                          {sc.label}
                        </Badge>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                          <Database className="size-3.5 text-muted-foreground" />
                        </div>
                        <span className="font-medium text-foreground">
                          {candidate.database_name || candidate.tenant_name || "-"}
                          {candidate.table_name ? `.${candidate.table_name}` : ""}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell
                      className="max-w-[400px] truncate text-muted-foreground"
                      title={candidate.latest_summary || "待深度分析"}
                    >
                      {candidate.latest_summary || "待深度分析"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {candidate.tags.length > 0
                          ? candidate.tags.map((tag) => (
                              <Badge key={tag.tag_label} variant="secondary" className="text-[10px] font-normal">
                                {tag.tag_label}
                              </Badge>
                            ))
                          : <span className="text-xs text-muted-foreground">-</span>}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-14 overflow-hidden rounded-full bg-muted/60">
                          <div
                            className={cn("h-full rounded-full transition-all duration-500", sc.dotBg)}
                            style={{ width: `${candidate.score}%` }}
                          />
                        </div>
                        <span className="text-xs tabular-nums text-muted-foreground">{candidate.score}</span>
                      </div>
                    </TableCell>
                    {isHistory ? (
                      <TableCell>
                        <Badge variant="outline" className="text-muted-foreground">
                          {candidate.lifecycle_status === "resolved" ? "已解决" : "已过期"}
                        </Badge>
                      </TableCell>
                    ) : null}
                    <TableCell className="text-xs tabular-nums text-muted-foreground">
                      {candidate.last_seen_at ? new Date(candidate.last_seen_at).toLocaleDateString() : "-"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={(e) => { e.stopPropagation(); openCandidate(candidate) }}
                      >
                        <ChevronRight className="size-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
        {!currentRiskLoading && currentRiskItems.length > 0 ? (
          <PaginationFooter
            page={currentRiskPage}
            pageSize={TABLE_PAGE_SIZE}
            total={currentRiskItems.length}
            onPageChange={setCurrentRiskPage}
            className="border-t border-border px-4 py-2"
          />
        ) : null}
      </ListTable>
    )
  }

  // ---- Main panel ----
  function renderDiagnosisPanel() {
    return (
      <div className="space-y-8">
        <section>
          {overviewData ? (
            <StatsOverviewCards
              cards={overviewData.cards}
              configChecks={overviewData.tenant_config_checks ?? []}
              warnings={overviewData.warnings}
              onTenantConfigClick={openTenantConfigInDrawer}
            />
          ) : loadingOverview ? (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <div
                  key={`stats-overview-skeleton-${index}`}
                  className="rounded-lg border border-border bg-card p-5 shadow-sm"
                >
                  <div className="h-3 w-24 rounded bg-muted/60" />
                  <div className="mt-3 h-7 w-20 rounded bg-muted/50" />
                  <div className="mt-3 h-3 w-32 rounded bg-muted/40" />
                </div>
              ))}
            </div>
          ) : null}
        </section>

        {(selectedDatasourceId || scopedDatasources.length > 0) ? (
          <div className="animate-in fade-in slide-in-from-bottom-1 duration-500 rounded-xl bg-card shadow-sm" style={{ animationDelay: "200ms" }}>
            <div className="flex items-center justify-between border-b border-border px-5 py-3">
              <div className="flex items-center gap-4">
                <Tabs value={topTab} onValueChange={(value) => setTopTab(value as TopTab)} className="w-fit">
                  <TabsList>
                    <TabsTrigger value={TOP_TAB_COLLECTION}>收集任务</TabsTrigger>
                    <TabsTrigger value={TOP_TAB_RISK}>风险检测</TabsTrigger>
                  </TabsList>
                </Tabs>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {topTab === TOP_TAB_COLLECTION
                    ? `${dailySummaries.length} 项结果`
                    : `${currentRiskItems.length} 项结果`}
                </span>
              </div>
              {topTab === TOP_TAB_RISK ? (
                <Tabs value={riskSubTab} onValueChange={(v) => setRiskSubTab(v as RiskSubTab)} className="w-fit">
                  <TabsList>
                    <TabsTrigger value="active">活跃风险</TabsTrigger>
                    <TabsTrigger value="history">历史</TabsTrigger>
                  </TabsList>
                </Tabs>
              ) : null}
            </div>

            {topTab === TOP_TAB_COLLECTION
              ? renderDailySummaryTable()
              : renderRiskCandidatesTable()}
          </div>
        ) : null}
      </div>
    )
  }

  const primary = renderDiagnosisPanel()

  const isDayOverviewDrawer = drawerView === "day_overview" && !!drawerDayDate && !effectiveIssue && !selectedRiskCandidate
  const isTenantConfigDrawer = drawerView === "tenant_config" && !!selectedTenantConfig
  const statsChatSessionKey = useMemo(() => {
    if (drawerMode !== "chat") return null
    if (!effectiveDatasourceId) return null
    if (!statsChatFocusObject) return null
    return JSON.stringify({ datasourceId: effectiveDatasourceId, focusObject: statsChatFocusObject })
  }, [drawerMode, effectiveDatasourceId, statsChatFocusObject])
  const drawerTitle = isTenantConfigDrawer
    ? `${selectedTenantConfig!.tenant_name} — 配置优化`
    : isDayOverviewDrawer
      ? `${fmtDateWithWeekday(drawerDayDate!)} 收集概览`
      : drawerDetail?.title || effectiveIssue?.title || (selectedRiskCandidate ? "风险详情" : "统计对象诊断")

  function renderDayOverviewPanel() {
    const day = drawerDaySummary
    return (
      <section className="space-y-4">
        {day ? (
          <>
            <div className="grid grid-cols-4 gap-3">
              {[
                { label: "总任务", value: day.total_tasks, color: "text-foreground" },
                { label: "成功表", value: day.success_tables, color: "text-positive" },
                { label: "失败表", value: day.failed_tables, color: "text-negative" },
                { label: "平均耗时", value: fmtDuration(day.avg_duration_min), color: "text-muted-foreground" },
              ].map((stat) => (
                <div key={stat.label} className="rounded-lg border border-border bg-muted/20 p-3 text-center">
                  <p className="text-[11px] text-muted-foreground">{stat.label}</p>
                  <p className={cn("mt-1 text-lg font-semibold tabular-nums", stat.color)}>{stat.value}</p>
                </div>
              ))}
            </div>

          </>
        ) : null}

        {/* Failed tables — primary */}
        <div>
          <h4 className="mb-2 text-sm font-medium text-foreground">
            失败表
            {dailyFailedTables.length > 0 ? (
              <Badge variant="destructive" className="ml-2 text-[11px]">{dailyFailedTables.length}</Badge>
            ) : null}
          </h4>
          {loadingDailyFailedTables ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="h-14 rounded-lg bg-muted/40 animate-pulse" />
              ))}
            </div>
          ) : dailyFailedTables.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-border bg-muted/10 py-8 text-center">
              <CalendarCheck className="size-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">当天所有采集任务均成功完成。</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              {dailyFailedTables.map((item) => {
                const isHigh = (item.latest_gather_seconds ?? 0) >= 1800
                return (
                  <button
                    key={`${item.cluster_key ?? ""}:${item.owner}.${item.table_name}`}
                    type="button"
                    className="flex w-full items-center gap-3 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:bg-muted/40"
                    onClick={() => openFailedTableInDrawer(item)}
                  >
                    <div className={cn(
                      "flex size-8 shrink-0 items-center justify-center rounded-md",
                      isHigh ? "bg-negative/15" : "bg-warning/15"
                    )}>
                      <Database className={cn("size-4", isHigh ? "text-negative" : "text-warning")} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">
                        {isAllMode ? (
                          <span className="text-muted-foreground">{failedTableDatasources.get(item.datasource_id ?? -1)?.cluster_key || item.cluster_key || "-"} / {failedTableDatasources.get(item.datasource_id ?? -1)?.name || (item.datasource_id ? String(item.datasource_id) : "-")} / {item.tenant_name || "-"} / </span>
                        ) : null}
                        {item.owner || "-"}{item.table_name ? `.${item.table_name}` : ""}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {item.latest_error || "收集失败"}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Badge variant="destructive" className="text-[10px]">x{item.failure_count}</Badge>
                      {item.latest_gather_seconds != null ? (
                        <span className="text-xs tabular-nums text-muted-foreground">{item.latest_gather_seconds}s</span>
                      ) : null}
                      <ChevronRight className="size-4 text-muted-foreground" />
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Task list — secondary, table with server-side pagination + filtering */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-sm font-medium text-foreground">
              采集任务
              {dailyTasksTotal > 0 ? (
                <span className="ml-2 text-xs font-normal text-muted-foreground">{dailyTasksTotal} 个</span>
              ) : null}
            </h4>
            <div className="flex items-center gap-2">
              <Select
                value={dailyTaskFilterType ?? "__all__"}
                onValueChange={(v) => { setDailyTaskFilterType(v === "__all__" ? null : v); setDailyTaskPage(1) }}
              >
                <SelectTrigger className="h-7 w-28 text-xs bg-card">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">全部类型</SelectItem>
                  <SelectItem value="AUTO">AUTO</SelectItem>
                  <SelectItem value="MANUAL">MANUAL</SelectItem>
                </SelectContent>
              </Select>
              <Select
                value={dailyTaskFilterStatus ?? "__all__"}
                onValueChange={(v) => { setDailyTaskFilterStatus(v === "__all__" ? null : v); setDailyTaskPage(1) }}
              >
                <SelectTrigger className="h-7 w-28 text-xs bg-card">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">全部状态</SelectItem>
                  <SelectItem value="SUCCESS">SUCCESS</SelectItem>
                  <SelectItem value="FAILED">FAILED</SelectItem>
                  <SelectItem value="RUNNING">RUNNING</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          {(() => {
            const taskColCount = isAllMode ? 9 : 6
            const taskHeaders = (
              <TableRow>
                {isAllMode ? <TableHead>集群</TableHead> : null}
                {isAllMode ? <TableHead>数据源</TableHead> : null}
                {isAllMode ? <TableHead>租户</TableHead> : null}
                <TableHead>状态</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>开始</TableHead>
                <TableHead>结束</TableHead>
                <TableHead className="text-right">耗时</TableHead>
                <TableHead className="text-right">表数</TableHead>
              </TableRow>
            )
            if (loadingDailyTasks) {
              return (
                <ListTable>
                  <Table>
                    <TableHeader>{taskHeaders}</TableHeader>
                    <TableBody>
                      <ListTableLoadingRows rowCount={5} columnCount={taskColCount} />
                    </TableBody>
                  </Table>
                </ListTable>
              )
            }
            if (dailyTasks.length === 0) {
              return <p className="text-xs text-muted-foreground py-2">无任务记录</p>
            }
            return (
              <ListTable>
                <Table>
                  <TableHeader>{taskHeaders}</TableHeader>
                  <TableBody>
                    {dailyTasks.map((task, idx) => {
                      const isSuccess = task.status === "SUCCESS"
                      const taskDatasource = dailyTaskDatasources.get(task.datasource_id ?? -1) ?? null
                      return (
                        <TableRow key={task.task_id ?? idx}>
                          {isAllMode ? (
                            <TableCell className="text-xs text-muted-foreground">{taskDatasource?.cluster_key || task.cluster_key || "-"}</TableCell>
                          ) : null}
                          {isAllMode ? (
                            <TableCell className="text-xs text-muted-foreground">{taskDatasource?.name || (task.datasource_id ? String(task.datasource_id) : "-")}</TableCell>
                          ) : null}
                          {isAllMode ? (
                            <TableCell className="text-xs text-muted-foreground">{task.tenant_name || "-"}</TableCell>
                          ) : null}
                          <TableCell>
                            <Badge variant={isSuccess ? "secondary" : "destructive"} className="text-[10px]">
                              {task.status ?? "-"}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">{task.task_type ?? "-"}</TableCell>
                          <TableCell className="text-xs tabular-nums">{fmtTime(task.start_time)}</TableCell>
                          <TableCell className="text-xs tabular-nums">{fmtTime(task.end_time)}</TableCell>
                          <TableCell className="text-right text-xs tabular-nums">
                            {task.duration_seconds != null ? fmtDuration(task.duration_seconds / 60) : "-"}
                          </TableCell>
                          <TableCell className="text-right text-xs tabular-nums">
                            {task.table_count ?? "-"}
                            {(task.failed_count ?? 0) > 0 ? (
                              <span className="text-negative ml-1">({task.failed_count})</span>
                            ) : null}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
                {dailyTasksTotal > DRAWER_TASK_PAGE_SIZE ? (
                  <PaginationFooter
                    page={dailyTaskPage}
                    pageSize={DRAWER_TASK_PAGE_SIZE}
                    total={dailyTasksTotal}
                    onPageChange={setDailyTaskPage}
                    className="border-t border-border px-4 py-2"
                  />
                ) : null}
              </ListTable>
            )
          })()}
        </div>
      </section>
    )
  }

  function renderTenantConfigPanel() {
    const cfg = selectedTenantConfig
    if (!cfg) return null

    const issueDescriptions: Record<string, string> = {
      auto_gather_disabled: "该租户的自动统计信息收集功能已关闭。开启后，OceanBase 会在调度窗口内自动采集表的统计信息。",
      no_windows: "该租户所有调度窗口已关闭，即使自动采集已启用也不会执行。需要至少启用一个调度窗口。",
      no_recent_tasks: "该租户已启用自动采集且调度窗口已开启，但近期未产生任何采集任务。可能原因：窗口时间尚未到达、任务被跳过、或存在其他异常。",
      unreachable: "无法连接该租户数据源，所有配置检查均失败。请检查数据源连接配置、网络可达性以及租户是否正常运行。",
    }

    function handleCopy() {
      navigator.clipboard.writeText(cfg!.suggestion_sql).then(
        () => { setTenantSqlCopied(true); window.setTimeout(() => setTenantSqlCopied(false), 1200) },
        () => toast.error("复制失败")
      )
    }

    return (
      <section className="space-y-5">
        {/* Status overview */}
        <div className="grid grid-cols-3 gap-3">
          {[
            {
              label: "自动采集",
              value: cfg.auto_gather_enabled === true ? "已启用" : cfg.auto_gather_enabled === false ? "未启用" : "未知",
              color: cfg.auto_gather_enabled === true ? "text-positive" : cfg.auto_gather_enabled === false ? "text-negative" : "text-muted-foreground",
            },
            {
              label: "调度窗口",
              value: `${cfg.enabled_windows}/${cfg.total_windows}`,
              color: cfg.enabled_windows > 0 ? "text-positive" : "text-negative",
            },
            {
              label: "近期任务",
              value: String(cfg.recent_task_count),
              color: cfg.recent_task_count > 0 ? "text-positive" : "text-warning",
            },
          ].map((stat) => (
            <div key={stat.label} className="rounded-lg border border-border bg-muted/20 p-3 text-center">
              <p className="text-[11px] text-muted-foreground">{stat.label}</p>
              <p className={cn("mt-1 text-lg font-semibold tabular-nums", stat.color)}>{stat.value}</p>
            </div>
          ))}
        </div>

        {/* Issue description */}
        <div className="rounded-lg border border-border bg-muted/20 p-4">
          <p className="text-sm font-medium text-foreground mb-1">{cfg.issue_label}</p>
          <p className="text-xs text-muted-foreground leading-relaxed">{issueDescriptions[cfg.issue_type] ?? ""}</p>
        </div>

        {/* Suggestion SQL — §8 dark code block */}
        <section className="rounded-lg border border-border">
          <div className="flex items-center justify-between border-b border-[#2a2a3a] bg-[#1e1e2e] px-4 py-2.5 rounded-t-lg">
            <div className="flex items-center gap-2">
              <Code2 className="size-3.5 text-[#a78bfa]" />
              <span className="text-xs font-medium text-[#cdd6f4]">优化建议</span>
            </div>
            <button
              type="button"
              className="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] text-[#a6adc8] transition-colors hover:bg-[#313244] hover:text-[#cdd6f4]"
              onClick={handleCopy}
            >
              {tenantSqlCopied ? <Check className="size-3" /> : <Copy className="size-3" />}
              {tenantSqlCopied ? "已复制" : "复制"}
            </button>
          </div>
          <div className="overflow-x-auto bg-[#1e1e2e] p-4 rounded-b-lg">
            <pre className="text-[13px] leading-relaxed font-mono">
              <code className="text-[#cdd6f4] whitespace-pre-wrap">{cfg.suggestion_sql}</code>
            </pre>
          </div>
        </section>
      </section>
    )
  }

  function renderDetailPanel() {
    return (
      <section className="space-y-3">
        {drawerView === "table_detail" && drawerDayDate ? (
          <button
            type="button"
            className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => {
              setSyntheticIssue(null)
              setSelectedIssueId(null)
              setDrawerView("day_overview")
              setDrawerMode("info")
            }}
          >
            <ArrowLeft className="size-3" />
            {fmtDateWithWeekday(drawerDayDate)} 概览
          </button>
        ) : null}
        <div className="rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          {drawerDetail?.summary || effectiveIssue?.summary || selectedRiskCandidate?.latest_summary || "当前对象详情"}
        </div>

        {drawerDetailLoading ? (
          <div className="rounded-lg border border-border bg-card p-6 text-center text-sm text-muted-foreground">
            <Loader2 className="mx-auto mb-2 size-4 animate-spin" />
            正在加载详情事实...
          </div>
        ) : drawerDetailError ? (
          <div className="rounded-lg border border-border bg-card p-4 text-sm text-destructive">
            详情加载失败：{drawerDetailError}
          </div>
        ) : !effectiveDatasourceId ? (
          <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            请先选择具体数据源以查看详情。
          </div>
        ) : !drawerDetail && !drawerDetailLoading ? (
          <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            暂无详情数据。
          </div>
        ) : null}

        {(drawerDetail?.sections || []).map((section) => (
          <section key={section.key} className="rounded-lg border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 space-y-1">
              <h3 className="text-sm font-semibold text-foreground">{section.title}</h3>
              {section.description ? <p className="text-xs text-muted-foreground">{section.description}</p> : null}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {section.fields.map((field) => (
                <div key={`${section.key}:${field.label}`} className="rounded-lg border border-border bg-muted/20 p-3">
                  <p className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground">{field.label}</p>
                  <p className="mt-1 text-sm text-foreground break-words">{field.value}</p>
                </div>
              ))}
            </div>
          </section>
        ))}

        {drawerDetail?.history_rows?.length ? (
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">收集历史采样</h3>
            <ListTable>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>开始时间</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>错误码</TableHead>
                    <TableHead className="text-right">耗时(秒)</TableHead>
                    <TableHead>触发方式</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {drawerDetail.history_rows.map((row, index) => {
                    const status = row.status || (row.ret_code && row.ret_code !== "0" ? "FAILED" : row.ret_code === "0" ? "SUCCESS" : "-")
                    const retCode = row.ret_code && row.ret_code !== "0" ? row.ret_code : null
                    return (
                      <TableRow key={`${row.task_id || row.start_time || "history"}:${index}`}>
                        <TableCell className="whitespace-nowrap">{row.start_time || "-"}</TableCell>
                        <TableCell>
                          <span className={status === "FAILED" ? "text-negative" : status === "SUCCESS" ? "text-positive" : ""}>
                            {status}
                          </span>
                        </TableCell>
                        <TableCell>
                          {retCode ? (
                            <code className="rounded bg-[#1e1e2e] px-1.5 py-0.5 font-mono text-xs text-[#f38ba8]">{retCode}</code>
                          ) : (
                            <span className="text-xs text-muted-foreground">-</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{row.gather_seconds ?? "-"}</TableCell>
                        <TableCell>{row.trigger_type || "-"}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </ListTable>
          </section>
        ) : null}

        {drawerDetail?.missing_facts?.length ? (
          <div
            className="rounded border border-border bg-muted/30 p-3 text-xs text-muted-foreground"
            aria-label="缺失事实"
          >
            缺失事实：{drawerDetail.missing_facts.join(" / ")}
          </div>
        ) : null}

        {hasRiskContext ? (
          <div className="rounded border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
            候选标签：{(selectedRiskCandidate?.tags || []).map((tag) => tag.tag_label).join(" / ") || "-"}
          </div>
        ) : null}
      </section>
    )
  }

  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />
      <Drawer open={drawerOpen} onOpenChange={setDrawerOpen}>
        <DrawerContent className="flex max-w-[920px] flex-col" showCloseButton={false} aria-describedby={undefined}>
          <DrawerHeader className="shrink-0 border-b border-border px-5 py-3">
            <div className="flex items-center justify-between">
              <DrawerTitle className="min-w-0 truncate text-sm font-semibold">{drawerTitle}</DrawerTitle>
              <div className="flex shrink-0 items-center gap-2">
                <Tabs value={drawerMode} onValueChange={(v) => setDrawerMode(v as DrawerMode)}>
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
          <DrawerBody className="flex min-h-0 flex-1 flex-col p-0">
            {drawerMode === "chat" ? (
              <SceneAgentChatShell
                className="min-h-0 flex-1 px-4 pb-3"
                adapter={statsChatAdapter}
                datasourceId={effectiveDatasourceId}
                focusObject={statsChatFocusObject}
                suggestedPrompt={chatSuggestedPrompt}
                submitSuggestedPrompt={true}
                freshSessionKey={statsChatSessionKey}
                onSuggestedPromptApplied={() => setChatSuggestedPrompt(null)}
                onJumpToFocusObject={handleJumpToFocusObject}
                embeddedInDrawer={true}
              />
            ) : isTenantConfigDrawer ? (
              <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">{renderTenantConfigPanel()}</div>
            ) : isDayOverviewDrawer ? (
              <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">{renderDayOverviewPanel()}</div>
            ) : (effectiveIssue || selectedRiskCandidate) ? (
              <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">{renderDetailPanel()}</div>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-4 py-8 text-muted-foreground">
                <Database className="size-8 opacity-40" />
                <p className="text-sm">请选择一个对象查看详情</p>
              </div>
            )}
          </DrawerBody>
        </DrawerContent>
      </Drawer>
    </>
  )
}
