import { useEffect, useMemo, useState } from "react"
import { ClipboardCopy, Code2, Database, Loader2, RefreshCw, Search, X } from "lucide-react"
import { toast } from "sonner"

import { useShellI18n, type ShellCopyKey, type ShellTranslatorFn } from "@/i18n/shellI18n"
import { SceneAgentChatShell } from "@/components/shared/PageAgentChatShell"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { Button } from "@/components/ui/button"
import {
  Drawer,
  DrawerBody,
  DrawerClose,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TimeRangePicker } from "@/components/shared/TimeRangePicker"
import { datasourcesApi, sqlAnalysisApi } from "@/lib/api"
import type { SceneBusinessAgentAdapter } from "@/components/shared/pageAgentAdapter"
import { cn } from "@/lib/utils"
import type {
  DataSource,
  SqlAnalysisCategory,
  SqlAnalysisListItem,
  SqlLiveAnalysisContext,
  SqlPlanExplainResponse,
} from "@/lib/api"

const TIME_PRESETS = [
  { labelKey: "sqlAnalysis.timePreset.15min" as const, minutes: 15 },
  { labelKey: "sqlAnalysis.timePreset.30min" as const, minutes: 30 },
  { labelKey: "sqlAnalysis.timePreset.1h" as const, minutes: 60 },
  { labelKey: "sqlAnalysis.timePreset.6h" as const, minutes: 360 },
  { labelKey: "sqlAnalysis.timePreset.24h" as const, minutes: 1440 },
]

const CATEGORY_OPTIONS: { value: SqlAnalysisCategory; labelKey: ShellCopyKey | "" }[] = [
  { value: "top_sql", labelKey: "" },
  { value: "slow_sql", labelKey: "sqlAnalysis.category.slowSql" },
]


const ALL_CLUSTERS = "__all_clusters__"
const ALL_DATASOURCES = "__all_datasources__"
const ALL_NON_SYSTEM_DBS = "__all_non_system_dbs__"

const EXPLAIN_SOURCE_LABEL_KEYS: Record<string, string> = {
  explain_sql: "EXPLAIN SQL",
  plan_cache_explain: "Plan Cache Explain",
  unavailable: "sqlAnalysis.explainSource.unavailable",
}


function formatDurationUs(us?: number | null) {
  if (us == null) return "-"
  if (us < 1_000) return `${us} μs`
  if (us < 1_000_000) return `${(us / 1_000).toFixed(1)} ms`
  return `${(us / 1_000_000).toFixed(2)} s`
}

function formatTimeUs(value?: number | null) {
  if (!value) return "-"
  return new Date(value / 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function shortSqlId(sqlId: string) {
  return sqlId.length > 12 ? sqlId.slice(0, 12) : sqlId
}

function formatExplainSourceLabel(source: string | null | undefined, t: ShellTranslatorFn) {
  if (!source) return t("sqlAnalysis.explainSource.unavailable")
  const key = EXPLAIN_SOURCE_LABEL_KEYS[source]
  if (!key) return source
  // Non-i18n values (like "EXPLAIN SQL") are returned as-is by t() since they're not keys
  return key.startsWith("sqlAnalysis.") ? t(key as ShellCopyKey) : key
}


function formatLastActiveTime(value?: string | null) {
  if (!value) return "-"
  const raw = value.trim()
  if (!raw) return "-"
  if (/^\d{13,}$/.test(raw)) {
    return formatTimeUs(Number(raw))
  }
  const normalized = raw.replace(" ", "T").replace(/\.(\d{3})\d+/, ".$1")
  const hasTimezone = /[zZ]$|[+-]\d{2}:\d{2}$/.test(normalized)
  const candidate = hasTimezone ? normalized : `${normalized}Z`
  const date = new Date(candidate)
  if (Number.isNaN(date.getTime())) return raw
  return date.toLocaleString()
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message
  }
  return fallback
}

function buildSqlAnalysisSuggestedPrompts(sqlId: string, t: ShellTranslatorFn) {
  return [
    t("sqlAnalysis.suggestedPrompt.analyzeSignals").replace("{sqlId}", sqlId),
    t("sqlAnalysis.suggestedPrompt.prioritize"),
    t("sqlAnalysis.suggestedPrompt.summarize"),
  ]
}

function getSqlItemKey(item: Pick<SqlAnalysisListItem, "datasource_id" | "ob_tenant_id" | "sql_id">) {
  return `${item.datasource_id ?? "na"}:${item.ob_tenant_id ?? "na"}:${item.sql_id}`
}

type DrawerMode = "info" | "chat"

export function SqlAnalysisPage() {
  const { t } = useShellI18n()
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [selectedCluster, setSelectedCluster] = useState(ALL_CLUSTERS)
  const [selectedDatasourceScope, setSelectedDatasourceScope] = useState(ALL_DATASOURCES)
  const [loadingList, setLoadingList] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerMode, setDrawerMode] = useState<DrawerMode>("info")
  const [dbNames, setDbNames] = useState<string[]>([])
  const [selectedDbName, setSelectedDbName] = useState(ALL_NON_SYSTEM_DBS)
  const [keyword, setKeyword] = useState("")
  const [timeLabel, setTimeLabel] = useState(t("sqlAnalysis.recent1h"))
  const [customStart, setCustomStart] = useState("")
  const [customEnd, setCustomEnd] = useState("")
  const [startTimeUs, setStartTimeUs] = useState(() => (Date.now() - 60 * 60_000) * 1000)
  const [endTimeUs, setEndTimeUs] = useState(() => Date.now() * 1000)
  const [category, setCategory] = useState<SqlAnalysisCategory>("top_sql")
  const [items, setItems] = useState<SqlAnalysisListItem[]>([])
  const [selectedItem, setSelectedItem] = useState<SqlAnalysisListItem | null>(null)
  const [analysisContext, setAnalysisContext] = useState<SqlLiveAnalysisContext | null>(null)
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(null)
  const [selectedPlanExplain, setSelectedPlanExplain] = useState<SqlPlanExplainResponse | null>(null)
  const [listError, setListError] = useState<string | null>(null)

  const monitorDatasources = datasources
  const clusterOptions = useMemo(() => {
    const keys = Array.from(new Set(monitorDatasources.map((ds) => ds.cluster_key).filter(Boolean)))
    return keys.sort()
  }, [monitorDatasources])
  const scopedDatasourceOptions = useMemo(() => {
    const filtered = selectedCluster === ALL_CLUSTERS
      ? monitorDatasources
      : monitorDatasources.filter((ds) => ds.cluster_key === selectedCluster)
    return filtered
  }, [monitorDatasources, selectedCluster])
  const scopedDatasourceId = selectedDatasourceScope === ALL_DATASOURCES ? null : Number(selectedDatasourceScope)
  const selectedItemDatasourceId = selectedItem?.datasource_id ?? null
  const detailDatasourceId = selectedItemDatasourceId ?? scopedDatasourceId
  const scopedDatasource = useMemo(
    () => scopedDatasourceId != null ? monitorDatasources.find((item) => item.id === scopedDatasourceId) ?? null : null,
    [monitorDatasources, scopedDatasourceId]
  )
  const selectedDatasource = useMemo(
    () => monitorDatasources.find((item) => item.id === detailDatasourceId) ?? null,
    [monitorDatasources, detailDatasourceId]
  )

  const getWindow = () => ({ startTimeUs, endTimeUs })

  const applyQuickRange = (minutes: number) => {
    const end = Date.now() * 1000
    const start = end - minutes * 60 * 1_000_000
    const preset = TIME_PRESETS.find((p) => p.minutes === minutes)
    setStartTimeUs(start)
    setEndTimeUs(end)
    setTimeLabel(preset ? `${t("sqlAnalysis.recentPrefix")} ${t(preset.labelKey)}` : `${t("sqlAnalysis.recentPrefix")} ${minutes} ${t("sqlAnalysis.minutesSuffix")}`)
  }

  const applyCustomRange = () => {
    if (!customStart || !customEnd) return
    const start = new Date(customStart).getTime() * 1000
    const end = new Date(customEnd).getTime() * 1000
    if (Number.isNaN(start) || Number.isNaN(end) || start >= end) {
      toast.error(t("sqlAnalysis.toast.invalidTime"))
      return
    }
    setStartTimeUs(start)
    setEndTimeUs(end)
    setTimeLabel(t("sqlAnalysis.customTime"))
  }

  const loadDatasources = async () => {
    try {
      const data = await datasourcesApi.list()
      const filtered = data.sort((a, b) => a.id - b.id)
      setDatasources(filtered)

      const firstDs = filtered[0]
      setSelectedCluster((prev) => {
        if (prev !== ALL_CLUSTERS) return prev
        return firstDs?.cluster_key || ALL_CLUSTERS
      })
      setSelectedDatasourceScope((prev) => {
        if (prev !== ALL_DATASOURCES) {
          return filtered.some((ds) => String(ds.id) === prev) ? prev : firstDs ? String(firstDs.id) : ALL_DATASOURCES
        }
        return firstDs ? String(firstDs.id) : ALL_DATASOURCES
      })
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, t("sqlAnalysis.toast.loadDsFailed")))
    }
  }

  const loadDbNames = async (datasourceId: number) => {
    try {
      const { startTimeUs, endTimeUs } = getWindow()
      const data = await sqlAnalysisApi.listLiveDbNames({
        datasource_id: datasourceId,
        start_time_us: startTimeUs,
        end_time_us: endTimeUs,
      })
      if (data.items.length > 0) {
        setDbNames(data.items)
        return
      }
    } catch {
      // fall through to derive from list items
    }
    setDbNames([])
  }

  const loadList = async (
    keepSqlId?: string,
    explicitWindow?: { startTimeUs: number; endTimeUs: number },
    dbNameOverride?: string,
  ) => {
    if (scopedDatasourceId == null) return
    setLoadingList(true)
    setListError(null)
    const { startTimeUs, endTimeUs } = explicitWindow ?? getWindow()
    try {
      const response = await sqlAnalysisApi.listLiveCategory({
        category,
        datasource_id: scopedDatasourceId,
        start_time_us: startTimeUs,
        end_time_us: endTimeUs,
        db_name: dbNameOverride && dbNameOverride !== ALL_NON_SYSTEM_DBS
          ? dbNameOverride
          : !dbNameOverride && selectedDbName !== ALL_NON_SYSTEM_DBS
            ? selectedDbName
            : undefined,
        keyword: keyword.trim() || undefined,
        limit: 50,
      })
      setItems(response.items)
      setDbNames((prev) => {
        if (prev.length > 0) return prev
        const derived = Array.from(new Set(response.items.map((i) => i.db_name).filter(Boolean))) as string[]
        return derived.sort()
      })
      const nextSelected = keepSqlId ? response.items.find((item) => item.sql_id === keepSqlId) ?? null : null
      setSelectedItem(nextSelected)
      setAnalysisContext(null)
      setSelectedPlanId(null)
      setSelectedPlanExplain(null)
      setDrawerOpen(false)
    } catch (error: unknown) {
      setItems([])
      setSelectedItem(null)
      setAnalysisContext(null)
      setDrawerOpen(false)
      const msg = getErrorMessage(error, t("sqlAnalysis.toast.loadListFailed"))
      setListError(msg)
      toast.error(msg)
    } finally {
      setLoadingList(false)
    }
  }

  const loadDetail = async (item: SqlAnalysisListItem) => {
    const datasourceId = item.datasource_id ?? scopedDatasourceId
    if (!datasourceId) return
    setLoadingDetail(true)
    const { startTimeUs, endTimeUs } = getWindow()
    try {
      const data = await sqlAnalysisApi.buildLiveContext({
        datasource_id: datasourceId,
        sql_id: item.sql_id,
        start_time_us: startTimeUs,
        end_time_us: endTimeUs,
        tenant_id: item.ob_tenant_id ?? undefined,
      })
      setAnalysisContext(data)
      const initialPlanId = data.current_plan_id ?? data.facts.current_plan?.plan_id ?? data.current_plans[0]?.plan_id ?? null
      setSelectedPlanId(initialPlanId)
      setSelectedPlanExplain(data.plan_explain ?? null)
    } catch (error: unknown) {
      setAnalysisContext(null)
      setSelectedPlanId(null)
      setSelectedPlanExplain(null)
      toast.error(getErrorMessage(error, t("sqlAnalysis.toast.loadDetailFailed")))
    } finally {
      setLoadingDetail(false)
    }
  }

  const loadSelectedPlanExplain = async (planId: number | null) => {
    if (!detailDatasourceId || !selectedItem) {
      setSelectedPlanExplain(null)
      return
    }
    const sqlText = analysisContext?.facts?.sql_text || selectedItem.sql_text || undefined
    const dbName = analysisContext?.facts?.db_name || selectedItem.db_name || undefined
    try {
      const explain = await sqlAnalysisApi.getLivePlanExplain({
        datasource_id: detailDatasourceId,
        sql_id: selectedItem.sql_id,
        plan_id: planId ?? undefined,
        sql_text: sqlText,
        db_name: dbName,
      })
      setSelectedPlanExplain(explain)
      if (explain.source === "unavailable" || !explain.items.length) {
        toast.error(t("sqlAnalysis.toast.explainUnavailable"))
      }
    } catch (error: unknown) {
      setSelectedPlanExplain(null)
      toast.error(getErrorMessage(error, t("sqlAnalysis.toast.loadExplainFailed")))
    }
  }

  const handleOpenDetail = async (item: SqlAnalysisListItem) => {
    setSelectedItem(item)
    setDrawerOpen(true)
    setDrawerMode("info")
    await loadDetail(item)
  }

  // ── Drawer Chat mode ───────────────────────────────────────────────────────
  const chatSceneKey = useMemo(() => {
    if (!selectedItem || !selectedDatasource) return ""
    return `sql-${selectedDatasource.id}-${selectedItem.sql_id}`
  }, [selectedDatasource, selectedItem])

  const chatContext = useMemo(() => {
    if (!selectedDatasource || !selectedItem || !analysisContext) return undefined
    return {
      datasource: {
        id: selectedDatasource.id,
        name: selectedDatasource.name,
        cluster_key: selectedDatasource.cluster_key,
        tenant_id: analysisContext.facts?.tenant_id ?? null,
        db_name: analysisContext.facts?.db_name || selectedItem.db_name || null,
        user_name: analysisContext.facts?.user_name || null,
      },
      focus: {
        kind: "sql",
        sql_id: selectedItem.sql_id,
        db_name: analysisContext.facts?.db_name || selectedItem.db_name || null,
        user_name: analysisContext.facts?.user_name || null,
      },
      sql_text: analysisContext.facts?.sql_text || selectedItem.sql_text || null,
      signals: analysisContext.signals,
      current_plans: analysisContext.current_plans.slice(0, 3).map((p) => ({
        plan_id: p.plan_id,
        plan_hash: p.plan_hash,
        table_scan: p.table_scan,
        last_active_time: p.last_active_time,
      })),
      ai_summary: null,
      risk_points: [],
    } as Record<string, unknown>
  }, [selectedDatasource, selectedItem, analysisContext])

  const chatSuggestions = useMemo(() => {
    if (!selectedItem) return []
    return buildSqlAnalysisSuggestedPrompts(selectedItem.sql_id, t)
  }, [selectedItem, t])

  const sqlChatFocusObject = useMemo(() => chatContext?.focus as Record<string, unknown> | null ?? null, [chatContext])
  const sqlChatSuggestedPrompt = useMemo(() => {
    if (drawerMode !== "chat" || !selectedItem || !analysisContext) return null
    return t("sqlAnalysis.suggestedPrompt.chatDefault").replace("{sqlId}", selectedItem.sql_id)
  }, [drawerMode, selectedItem, analysisContext, t])
  const sqlChatSessionKey = useMemo(() => {
    if (drawerMode !== "chat") return null
    if (!chatSceneKey || !detailDatasourceId || !sqlChatFocusObject) return null
    return JSON.stringify({ datasourceId: detailDatasourceId, sceneKey: "sql_analysis", focusObject: sqlChatFocusObject })
  }, [drawerMode, chatSceneKey, detailDatasourceId, sqlChatFocusObject])
  const sqlChatAdapter = useMemo<SceneBusinessAgentAdapter>(() => ({
    page: "sql-analysis",
    sceneKey: chatSceneKey || "sql_analysis",
    title: t("sqlAnalysis.chatTitle"),
    placeholder: t("sqlAnalysis.chatPlaceholder"),
    conversationTitle: selectedItem ? `SQL Analysis · ${shortSqlId(selectedItem.sql_id)}` : "SQL Analysis",
    suggestions: chatSuggestions,
    skills: ["sql_analysis"],
    buildContext: () => chatContext ?? {},
  }), [selectedItem, chatSuggestions, chatContext])

  useEffect(() => {
    void loadDatasources()
  }, [])

  useEffect(() => {
    if (!monitorDatasources.length) return
    if (selectedDatasourceScope === ALL_DATASOURCES) return
    if (scopedDatasourceOptions.some((item) => String(item.id) === selectedDatasourceScope)) return
    const first = scopedDatasourceOptions[0]
    setSelectedDatasourceScope(first ? String(first.id) : ALL_DATASOURCES)
  }, [monitorDatasources, scopedDatasourceOptions, selectedDatasourceScope])

  useEffect(() => {
    if (monitorDatasources.length === 0) return
    if (scopedDatasourceId == null) return
    setSelectedDbName(ALL_NON_SYSTEM_DBS)
    void loadDbNames(scopedDatasourceId)
    void loadList(undefined, undefined, ALL_NON_SYSTEM_DBS)
  }, [monitorDatasources.length, scopedDatasourceId])

  useEffect(() => {
    if (monitorDatasources.length > 0) {
      void loadList()
    }
  }, [category, startTimeUs, endTimeUs])

  const planHistory = analysisContext?.current_plans ?? []
  const planCount = analysisContext?.window_plan_total ?? planHistory.length
  const latestPlanId = analysisContext?.current_plan_id ?? analysisContext?.facts?.current_plan?.plan_id ?? null
  const latestPlanHash = analysisContext?.facts?.current_plan?.plan_hash ?? null
  const selectedPlanDetail = useMemo(() => {
    if (planHistory.length === 0) return null
    if (selectedPlanId == null) return planHistory[0]
    return planHistory.find((item) => item.plan_id === selectedPlanId) ?? planHistory[0]
  }, [planHistory, selectedPlanId])
  const planExplainItems = selectedPlanExplain?.items ?? []

  // ── Toolbar ─────────────────────────────────────────────────────────────────
  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup className="flex-1">
        <Select
          value={selectedCluster}
          onValueChange={(value) => {
            setSelectedCluster(value)
            const clusterDs = value === ALL_CLUSTERS
              ? monitorDatasources
              : monitorDatasources.filter((ds) => ds.cluster_key === value)
            const first = clusterDs[0]
            setSelectedDatasourceScope(first ? String(first.id) : ALL_DATASOURCES)
          }}
        >
          <SelectTrigger className="w-44 bg-card">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_CLUSTERS}>{t("sqlAnalysis.allClusters")}</SelectItem>
            {clusterOptions.map((ck) => (
              <SelectItem key={ck} value={ck}>{ck}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={selectedDatasourceScope}
          onValueChange={setSelectedDatasourceScope}
          disabled={scopedDatasourceOptions.length === 0}
        >
          <SelectTrigger aria-label={t("sqlAnalysis.dsFilterAria")} className="w-40 bg-card">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_DATASOURCES}>{t("sqlAnalysis.allDatasources")}</SelectItem>
            {scopedDatasourceOptions.map((ds) => (
              <SelectItem key={ds.id} value={String(ds.id)}>
                {ds.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={selectedDbName}
          onValueChange={(value) => {
            setSelectedDbName(value)
            void loadList(selectedItem?.sql_id, undefined, value)
          }}
          disabled={loadingList || monitorDatasources.length === 0}
        >
          <SelectTrigger aria-label={t("sqlAnalysis.dbFilterAria")} className="w-48 bg-card">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_NON_SYSTEM_DBS}>{t("sqlAnalysis.allBusinessDbs")}</SelectItem>
            {dbNames.map((item) => (
              <SelectItem key={item} value={item}>
                {item}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void loadList()
            }}
            placeholder={t("sqlAnalysis.searchSqlText")}
            className="w-72 rounded-lg bg-card pl-9 text-sm"
          />
        </div>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <TimeRangePicker
          label={timeLabel}
          quickRanges={TIME_PRESETS.map((p) => ({ label: t(p.labelKey), minutes: p.minutes }))}
          customStart={customStart}
          customEnd={customEnd}
          onCustomStartChange={setCustomStart}
          onCustomEndChange={setCustomEnd}
          onSelectQuickRange={applyQuickRange}
          onApplyCustomRange={applyCustomRange}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => void loadList(selectedItem?.sql_id)}
          disabled={loadingList}
        >
          {loadingList ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {t("sqlAnalysis.refresh")}
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  // ── Table section ──────────────────────────────────────────────────────────
  const columnCount = 8

  const primary = (
    <section className="animate-in fade-in slide-in-from-bottom-1 duration-500">
      {/* Card header: tabs */}
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="flex items-center gap-4">
          <Tabs value={category} onValueChange={(v) => setCategory(v as SqlAnalysisCategory)} className="w-fit">
            <TabsList>
              {CATEGORY_OPTIONS.map((opt) => (
                <TabsTrigger key={opt.value} value={opt.value}>{opt.labelKey ? t(opt.labelKey) : "Top SQL"}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <span className="text-xs tabular-nums text-muted-foreground">
            {items.length} {t("sqlAnalysis.resultCount")}
          </span>
        </div>
      </div>
      {/* Card body: table */}
      <ListTable className="border-0 rounded-none">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("sqlAnalysis.col.sqlText")}</TableHead>
                <TableHead className="w-28">{t("sqlAnalysis.col.sqlId")}</TableHead>
                <TableHead className="w-24">{t("sqlAnalysis.col.cluster")}</TableHead>
                <TableHead className="w-28">{t("sqlAnalysis.col.datasource")}</TableHead>
                <TableHead className="w-28">{t("sqlAnalysis.col.database")}</TableHead>
                <TableHead className="w-24 text-right">{t("sqlAnalysis.col.executions")}</TableHead>
                <TableHead className="w-28 text-right">{t("sqlAnalysis.col.avgDuration")}</TableHead>
                <TableHead className="w-28 text-right">{t("sqlAnalysis.col.maxDuration")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loadingList ? (
                <ListTableLoadingRows rowCount={6} columnCount={columnCount} />
              ) : listError ? (
                <TableRow>
                  <TableCell colSpan={columnCount} className="h-32 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <Database className="size-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">{listError}</p>
                      <Button variant="ghost" size="sm" onClick={() => void loadList()}>
                        {t("sqlAnalysis.retry")}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ) : items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={columnCount} className="h-32 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <Database className="size-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">{t("sqlAnalysis.emptyWindow")}</p>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                items.map((item, index) => (
                  <TableRow
                    key={getSqlItemKey(item)}
                    className={cn(
                      "cursor-pointer transition-colors duration-150 hover:bg-muted/40",
                      selectedItem && getSqlItemKey(selectedItem) === getSqlItemKey(item) && drawerOpen && "bg-primary/[0.04]"
                    )}
                    style={{ animationDelay: `${index * 30}ms` }}
                    onClick={() => void handleOpenDetail(item)}
                  >
                    <TableCell className="min-w-0 max-w-0">
                      <div className="truncate text-sm text-foreground" title={item.sql_text || undefined}>
                        {item.sql_text || "NA"}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{shortSqlId(item.sql_id)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{scopedDatasource?.cluster_key || "-"}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{scopedDatasource?.name || "-"}</TableCell>
                    <TableCell>{item.db_name || "-"}</TableCell>
                    <TableCell className="text-right tabular-nums">{item.executions ?? 0}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatDurationUs(item.avg_elapsed_time_us)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatDurationUs(item.max_elapsed_time_us)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </ListTable>
    </section>
  )

  // ── Drawer ─────────────────────────────────────────────────────────────────
  function renderDrawer() {
    const title = selectedItem
      ? `${selectedItem.db_name || "-"} · ${shortSqlId(selectedItem.sql_id)}`
      : t("sqlAnalysis.drawerTitle")

    return (
      <Drawer open={drawerOpen} onOpenChange={setDrawerOpen}>
        <DrawerContent className="flex max-w-[780px] flex-col" showCloseButton={false} aria-describedby={undefined}>
          <DrawerHeader className="shrink-0 border-b border-border px-5 py-3">
            <div className="flex items-center justify-between gap-3">
              <DrawerTitle className="min-w-0 truncate text-sm font-semibold">{title}</DrawerTitle>
              <div className="flex shrink-0 items-center gap-2">
                <Tabs value={drawerMode} onValueChange={(v) => setDrawerMode(v as DrawerMode)}>
                  <TabsList>
                    <TabsTrigger value="info">{t("sqlAnalysis.tab.info")}</TabsTrigger>
                    <TabsTrigger value="chat">{t("sqlAnalysis.tab.aiAnalysis")}</TabsTrigger>
                  </TabsList>
                </Tabs>
                <DrawerClose className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground" aria-label={t("sqlAnalysis.closeAria")}>
                  <X className="size-4" />
                </DrawerClose>
              </div>
            </div>
          </DrawerHeader>
          <DrawerBody className="flex min-h-0 flex-1 flex-col p-0">
            {drawerMode === "chat" ? (
              <SceneAgentChatShell
                className="min-h-0 flex-1 px-4 pb-3"
                adapter={sqlChatAdapter}
                datasourceId={detailDatasourceId}
                focusObject={sqlChatFocusObject}
                suggestedPrompt={sqlChatSuggestedPrompt}
                submitSuggestedPrompt={false}
                freshSessionKey={sqlChatSessionKey}
                onSuggestedPromptApplied={() => {}}
                embeddedInDrawer={true}
              />
            ) : (
            <div className="flex min-h-0 flex-1 flex-col space-y-6 overflow-y-auto px-4 py-4">
              {!selectedItem ? (
                <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
                  {t("sqlAnalysis.emptySelect")}
                </div>
              ) : loadingDetail ? (
                <div className="flex min-h-56 items-center justify-center text-sm text-muted-foreground">
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  {t("sqlAnalysis.loadingDetail")}
                </div>
              ) : analysisContext ? (
                <>
                  <section className="space-y-3">
                    <div className="text-sm font-medium text-foreground">{t("sqlAnalysis.overview")}</div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="rounded-lg border border-border bg-muted/20 p-4 text-sm">
                        <div className="text-xs text-muted-foreground">{t("sqlAnalysis.ownership")}</div>
                        <div className="mt-2 space-y-1 text-foreground">
                          <div>{t("sqlAnalysis.ownershipDs")}: {scopedDatasource?.name || "-"}</div>
                          <div>{t("sqlAnalysis.ownershipDb")}: {analysisContext.facts?.db_name || selectedItem.db_name || "-"}</div>
                          <div>{t("sqlAnalysis.ownershipUser")}: {analysisContext.facts?.user_name || "-"}</div>
                        </div>
                      </div>
                      <div className="rounded-lg border border-border bg-muted/20 p-4 text-sm">
                        <div className="text-xs text-muted-foreground">{t("sqlAnalysis.execSummary")}</div>
                        <div className="mt-2 space-y-1 text-foreground">
                          <div>{t("sqlAnalysis.execSqlId")}: {analysisContext.sql_id}</div>
                          <div>{t("sqlAnalysis.execLatestTime")}: {formatTimeUs(analysisContext.facts?.latest_request_time_us)}</div>
                          <div>{t("sqlAnalysis.execCount")}: {selectedItem.executions ?? "-"}</div>
                          <div>{t("sqlAnalysis.execAvgDuration")}: {formatDurationUs(selectedItem.avg_elapsed_time_us)}</div>
                        </div>
                      </div>
                    </div>
                    <section className="rounded-lg border border-border">
                      <div className="flex items-center justify-between border-b border-[#2a2a3a] bg-[#1e1e2e] px-4 py-2.5 rounded-t-lg">
                        <div className="flex items-center gap-2">
                          <Code2 className="size-3.5 text-[#a78bfa]" />
                          <span className="text-xs font-medium text-[#cdd6f4]">{t("sqlAnalysis.sqlStatement")}</span>
                        </div>
                        <button
                          type="button"
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] text-[#a6adc8] transition-colors hover:bg-[#313244] hover:text-[#cdd6f4]"
                          onClick={() => {
                            const text = analysisContext.facts?.sql_text || selectedItem.sql_text || ""
                            void navigator.clipboard.writeText(text)
                            toast.success(t("sqlAnalysis.copied"))
                          }}
                        >
                          <ClipboardCopy className="size-3" />
                          {t("sqlAnalysis.copy")}
                        </button>
                      </div>
                      <div className="overflow-x-auto bg-[#1e1e2e] p-4 rounded-b-lg">
                        <pre className="text-[13px] leading-relaxed font-mono">
                          <code className="text-[#cdd6f4]">{analysisContext.facts?.sql_text || selectedItem.sql_text || "-"}</code>
                        </pre>
                      </div>
                    </section>
                  </section>

                  {planHistory.length > 0 && (
                  <section className="space-y-3">
                    <div className="text-sm font-medium text-foreground">{t("sqlAnalysis.planHistory").replace("{count}", String(planCount))}</div>
                      <div className="overflow-x-auto rounded-lg border border-border">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>{t("sqlAnalysis.planCol.planId")}</TableHead>
                              <TableHead>{t("sqlAnalysis.planCol.planHash")}</TableHead>
                              <TableHead className="text-right">{t("sqlAnalysis.planCol.tableScan")}</TableHead>
                              <TableHead>{t("sqlAnalysis.planCol.lastActive")}</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {planHistory.map((item) => {
                              const isSelected = selectedPlanDetail?.plan_id === item.plan_id
                              const isCurrent = latestPlanId != null
                                && item.plan_id === latestPlanId
                                && (latestPlanHash == null || item.plan_hash === latestPlanHash)
                              return (
                                <TableRow
                                  key={`${item.plan_id}-${item.last_active_time}`}
                                  className={cn(
                                    "cursor-pointer transition-colors duration-150 hover:bg-muted/40",
                                    isSelected && "bg-primary/[0.04]"
                                  )}
                                  onClick={() => {
                                    const planId = item.plan_id ?? null
                                    setSelectedPlanId(planId)
                                    void loadSelectedPlanExplain(planId)
                                  }}
                                >
                                  <TableCell>
                                    <div className="inline-flex items-center gap-2">
                                      <span>{item.plan_id}</span>
                                      {isCurrent ? (
                                        <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">{t("sqlAnalysis.currentPlan")}</span>
                                      ) : null}
                                    </div>
                                  </TableCell>
                                  <TableCell>{item.plan_hash ?? "-"}</TableCell>
                                  <TableCell className="text-right">{item.table_scan}</TableCell>
                                  <TableCell>{formatLastActiveTime(item.last_active_time)}</TableCell>
                                </TableRow>
                              )
                            })}
                          </TableBody>
                        </Table>
                      </div>
                  </section>
                  )}

                  <section className="space-y-3">
                    <div className="text-sm font-medium text-foreground">{t("sqlAnalysis.explainDetail")}</div>
                    {planExplainItems.length ? (
                      <div className="overflow-x-auto rounded-lg border border-border">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>{t("sqlAnalysis.explainCol.operator")}</TableHead>
                              <TableHead>{t("sqlAnalysis.explainCol.object")}</TableHead>
                              <TableHead className="text-right">{t("sqlAnalysis.explainCol.cost")}</TableHead>
                              <TableHead className="text-right">{t("sqlAnalysis.explainCol.cardinality")}</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {planExplainItems.map((item, index) => (
                              <TableRow key={`${item.operator}-${index}`}>
                                <TableCell>{item.operator}</TableCell>
                                <TableCell>{item.object_name || "-"}</TableCell>
                                <TableCell className="text-right">{item.cost ?? "-"}</TableCell>
                                <TableCell className="text-right">{item.cardinality ?? "-"}</TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    ) : (
                      <div className="rounded-lg border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
                        <p>{t("sqlAnalysis.noExplainUnavailable")}</p>
                        {(analysisContext?.facts?.sql_text || selectedItem.sql_text) && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="mt-3"
                            onClick={() => void loadSelectedPlanExplain(selectedPlanId)}
                          >
                            {t("sqlAnalysis.fetchExplain")}
                          </Button>
                        )}
                      </div>
                    )}
                  </section>
                </>
              ) : (
                <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
                  {t("sqlAnalysis.noDetail")}
                </div>
              )}
            </div>
            )}
          </DrawerBody>
        </DrawerContent>
      </Drawer>
    )
  }

  return (
    <>
      <WorkbenchPage
        toolbar={
          <div className="rounded-xl bg-card p-4 shadow-sm">
            {toolbar}
          </div>
        }
        primary={
          <div className="rounded-xl bg-card shadow-sm">
            {primary}
          </div>
        }
      />
      {renderDrawer()}
    </>
  )
}
