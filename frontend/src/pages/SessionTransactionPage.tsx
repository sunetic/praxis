import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, RefreshCw, Search, Users, X, Zap } from "lucide-react"
import { toast } from "sonner"

import { SessionTransactionOverview } from "@/components/page/SessionTransactionOverview"
import { SceneAgentChatShell } from "@/components/shared/PageAgentChatShell"
import type { SceneBusinessAgentAdapter } from "@/components/shared/pageAgentAdapter"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Drawer, DrawerBody, DrawerClose, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from "@/components/ui/drawer"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  type DataSource,
  type LiveSession,
  type LiveTransaction,
  datasourcesApi,
  sessionAnalysisApi,
} from "@/lib/api"
import { cn } from "@/lib/utils"

const DEFAULT_REFRESH_INTERVAL_MS = 30_000
const REFRESH_INTERVAL_OPTIONS = [
  { value: 15_000, label: "15 秒" },
  { value: 30_000, label: "30 秒" },
  { value: 60_000, label: "60 秒" },
]
const SESSION_PAGE_SIZE = 20
const TXN_PAGE_SIZE = 10
const ALL_CLUSTERS = "__all_clusters__"

function fmtSeconds(s: number): string {
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m < 60) return `${m}m ${rem}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

function sessionIdentity(session: LiveSession): string {
  return session.identity_label || (session.tenant_name ? `${session.user}@${session.tenant_name}` : session.user)
}

function buildTopDistribution(entries: Record<string, number>) {
  return Object.entries(entries)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 5)
}

async function readAnalysisStream(
  response: Response,
  handlers: {
    onText: (text: string) => void
    onDone: () => void
    onError: (message: string) => void
  }
): Promise<void> {
  if (!response.ok) {
    handlers.onError(`AI 分析失败（${response.status}）`)
    handlers.onDone()
    return
  }
  const reader = response.body?.getReader()
  if (!reader) {
    handlers.onDone()
    return
  }
  const decoder = new TextDecoder()
  let buffer = ""
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""
    for (const line of lines) {
      const payloadLine = line.trimStart()
      if (!payloadLine.startsWith("data:")) continue
      try {
        const event = JSON.parse(payloadLine.replace(/^data:\s*/, ""))
        if (event.type === "text") handlers.onText(String(event.data || ""))
        if (event.type === "error") handlers.onError(String(event.data || "AI 分析失败"))
        if (event.type === "done") handlers.onDone()
      } catch {
        handlers.onError("AI 分析返回了无法解析的事件")
      }
    }
  }
  handlers.onDone()
}


function SessionBadge({ state }: { state: string }) {
  if (state === "ACTIVE") {
    return (
      <Badge
        variant="outline"
        className="border-positive text-positive"
      >
        ACTIVE
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      SLEEP
    </Badge>
  )
}

function TxnBadge({ state }: { state: string }) {
  if (state === "PENDING_COMMIT") {
    return (
      <Badge
        variant="outline"
        className="border-negative text-negative"
      >
        PENDING COMMIT
      </Badge>
    )
  }
  return (
    <Badge
      variant="outline"
      className="border-warning text-warning"
    >
      LONG TXN
    </Badge>
  )
}

function KillConfirmDialog({
  open,
  sessionId,
  currentSql,
  onConfirm,
  onCancel,
  loading,
}: {
  open: boolean
  sessionId: number | null
  currentSql: string | null
  onConfirm: () => void
  onCancel: () => void
  loading: boolean
}) {
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onCancel()}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>确认终止会话</DialogTitle>
          <DialogDescription>
            终止会话 <span className="font-mono font-medium">{sessionId}</span> 后，该会话的未提交事务将自动回滚。
          </DialogDescription>
        </DialogHeader>
        {currentSql ? (
          <div className="rounded-lg bg-muted p-3 font-mono text-xs text-muted-foreground line-clamp-3">
            {currentSql}
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={loading}>
            取消
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={loading}>
            {loading ? "终止中…" : "确认终止"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}


function AiAnalysisDrawer({
  open,
  onOpenChange,
  adapter,
  datasourceId,
  focusObject,
  freshSessionKey,
  suggestedPrompt,
  onSuggestedPromptApplied,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  adapter: SceneBusinessAgentAdapter
  datasourceId: number | null
  focusObject: Record<string, unknown> | null
  freshSessionKey: string | null
  suggestedPrompt: string | null
  onSuggestedPromptApplied: () => void
}) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent showCloseButton={false} className="flex max-w-[640px] flex-col">
        <DrawerHeader className="shrink-0 border-b border-border px-5 py-3">
          <div className="flex items-center justify-between gap-3">
            <DrawerTitle>AI 会话诊断</DrawerTitle>
            <DrawerClose asChild>
              <Button variant="ghost" size="icon" className="size-7">
                <X className="size-4" />
                <span className="sr-only">关闭</span>
              </Button>
            </DrawerClose>
          </div>
        </DrawerHeader>
        <DrawerBody className="flex min-h-0 flex-1 flex-col p-0">
          <SceneAgentChatShell
            adapter={adapter}
            datasourceId={datasourceId}
            focusObject={focusObject}
            freshSessionKey={freshSessionKey}
            suggestedPrompt={suggestedPrompt}
            submitSuggestedPrompt={true}
            onSuggestedPromptApplied={onSuggestedPromptApplied}
            embeddedInDrawer={true}
            className="min-h-0 flex-1 px-4 pb-3"
          />
        </DrawerBody>
      </DrawerContent>
    </Drawer>
  )
}

function TransactionDrawer({
  open,
  txn,
  onOpenChange,
  onKill,
}: {
  open: boolean
  txn: LiveTransaction | null
  onOpenChange: (open: boolean) => void
  onKill: (sessionId: number) => void
}) {
  if (!txn) return null

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent showCloseButton={false}>
        <DrawerHeader className="shrink-0 border-b border-border px-5 py-3">
          <div className="flex items-center justify-between gap-3">
            <DrawerTitle>{`事务 ${txn.trans_hash.slice(0, 12)}…`}</DrawerTitle>
            <DrawerClose asChild>
              <Button variant="ghost" size="icon" className="size-7">
                <X className="size-4" />
                <span className="sr-only">关闭</span>
              </Button>
            </DrawerClose>
          </div>
          <DrawerDescription>
            {`会话 ${txn.session_id ?? "—"} · ${txn.trans_type} · 已运行 ${fmtSeconds(txn.elapsed_seconds)}`}
          </DrawerDescription>
        </DrawerHeader>
        <DrawerBody>
          <TransactionDrawerBody key={`${txn.trans_hash}:${txn.state}:${txn.elapsed_seconds}`} txn={txn} onKill={onKill} />
        </DrawerBody>
      </DrawerContent>
    </Drawer>
  )
}

function TransactionDrawerBody({
  txn,
  onKill,
}: {
  txn: LiveTransaction
  onKill: (sessionId: number) => void
}) {
  const [aiText, setAiText] = useState("")
  const [aiLoading, setAiLoading] = useState(txn.sql_list.length > 0)
  const [aiError, setAiError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (txn.sql_list.length === 0) return
    const controller = new AbortController()
    abortRef.current = controller
    const snapshot = {
      total: 0,
      active: 0,
      long_transaction_count: 1,
      pending_transaction_count: txn.state === "PENDING_COMMIT" ? 1 : 0,
      user_distribution: {},
      ip_distribution: {},
      long_transactions: [
        {
          trans_type: txn.trans_type,
          elapsed_seconds: txn.elapsed_seconds,
          sql_list: txn.sql_list,
        },
      ],
    }

    sessionAnalysisApi
      .analyzeStream(snapshot, controller.signal)
      .then((response) =>
        readAnalysisStream(response, {
          onText: (text) => setAiText((current) => current + text),
          onDone: () => setAiLoading(false),
          onError: (message) => {
            setAiError(message)
            setAiLoading(false)
          },
        })
      )
      .catch((error: unknown) => {
        if ((error as { name?: string })?.name === "AbortError") return
        setAiError("AI 分析失败，请稍后重试")
        setAiLoading(false)
      })

    return () => controller.abort()
  }, [txn])

  return (
    <div className="space-y-5">
      <div className="space-y-5">
        <div className="flex items-center gap-2">
          <TxnBadge state={txn.state} />
          {txn.session_id ? (
            <Button variant="destructive" size="sm" onClick={() => onKill(txn.session_id!)}>
              Kill Session
            </Button>
          ) : null}
        </div>

        <section className="space-y-2">
          <h3 className="text-sm font-medium text-foreground">事务 SQL 样本</h3>
          {txn.sql_list.length === 0 ? (
            <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
              当前没有抓到事务内 SQL 样本，暂时只能依据事务状态与耗时判断风险。
            </div>
          ) : (
            <div className="space-y-2">
              {txn.sql_list.map((sql, index) => (
                <div
                  key={`${txn.trans_hash}-${index}`}
                  className="rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs text-muted-foreground"
                >
                  {sql}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="space-y-2">
          <div className="text-sm font-medium text-foreground">AI 事务解读</div>
          <div className="rounded-lg border border-border bg-muted/30 p-3 text-sm">
            {aiLoading ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <span className="animate-pulse">●</span>
                AI 正在基于事务 SQL 样本生成判断…
              </div>
            ) : aiError ? (
              <div className="text-negative">{aiError}</div>
            ) : aiText ? (
              <div className="whitespace-pre-wrap text-foreground">{aiText}</div>
            ) : (
              <div className="text-muted-foreground">
                {txn.sql_list.length === 0 ? "等待 SQL 样本后再触发 AI 解读。" : "暂无分析结果。"}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

export function SessionTransactionPage() {
  const [selectedClusterKey, setSelectedClusterKey] = useState(ALL_CLUSTERS)

  const [sessions, setSessions] = useState<LiveSession[]>([])
  const [sessionsTotal, setSessionsTotal] = useState(0)
  const [sessionsActive, setSessionsActive] = useState(0)
  const [longTxns, setLongTxns] = useState<LiveTransaction[]>([])
  const [pendingTxns, setPendingTxns] = useState<LiveTransaction[]>([])

  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [txnsLoading, setTxnsLoading] = useState(false)
  const [sessionsError, setSessionsError] = useState<string | null>(null)
  const [txnsError, setTxnsError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const [sessionScope, setSessionScope] = useState<"active" | "all">("active")
  const [selectedDatasourceScope, setSelectedDatasourceScope] = useState("all")
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true)
  const [refreshIntervalMs, setRefreshIntervalMs] = useState(DEFAULT_REFRESH_INTERVAL_MS)
  const [sessionKeyword, setSessionKeyword] = useState("")
  const [sessionPage, setSessionPage] = useState(1)
  const [txnTab, setTxnTab] = useState<"long" | "pending">("long")
  const [txnPage, setTxnPage] = useState(1)

  const [aiDrawerOpen, setAiDrawerOpen] = useState(false)
  const [chatSuggestedPrompt, setChatSuggestedPrompt] = useState<string | null>(null)

  const [killTarget, setKillTarget] = useState<{ datasourceId: number; sessionId: number; currentSql: string | null } | null>(null)
  const [killing, setKilling] = useState(false)

  const [drawerTxn, setDrawerTxn] = useState<LiveTransaction | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const [allDatasources, setAllDatasources] = useState<DataSource[]>([])
  const [datasourcesLoading, setDatasourcesLoading] = useState(true)
  const [datasourcesError, setDatasourcesError] = useState<string | null>(null)

  const loadDatasources = useCallback(async () => {
    setDatasourcesLoading(true)
    setDatasourcesError(null)
    try {
      const items = await datasourcesApi.list()
      setAllDatasources(items)
      if (items.length === 0) {
        setSelectedClusterKey(ALL_CLUSTERS)
        return
      }
      setSelectedClusterKey((current) => {
        if (!current || current === ALL_CLUSTERS) return ALL_CLUSTERS
        return items.some((item) => item.cluster_key === current) ? current : ALL_CLUSTERS
      })
    } catch {
      setAllDatasources([])
      setSelectedClusterKey(ALL_CLUSTERS)
      setDatasourcesError("数据源加载失败")
    } finally {
      setDatasourcesLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadDatasources()
  }, [loadDatasources])

  const liveDatasources = allDatasources.filter((ds) => ds.db_type === "oceanbase")

  const clusterOptions = useMemo(
    () => Array.from(new Set(liveDatasources.map((item) => item.cluster_key).filter((item) => item.trim().length > 0))).sort(),
    [liveDatasources]
  )

  const scopedDatasources = useMemo(
    () =>
      selectedClusterKey === ALL_CLUSTERS
        ? liveDatasources
        : liveDatasources.filter((item) => item.cluster_key === selectedClusterKey),
    [liveDatasources, selectedClusterKey]
  )
  const selectedDatasourceId = useMemo(() => {
    if (!scopedDatasources.length) return null
    const sysDatasource = scopedDatasources.find((item) => item.tenant_role === "sys")
    if (sysDatasource) return sysDatasource.id
    return [...scopedDatasources].sort((left, right) => left.id - right.id)[0].id
  }, [scopedDatasources])

  const datasourceOptions = useMemo(() => {
    const options: Array<{ value: string; label: string; tenantId?: number }> = [
      { value: "all", label: "全部数据源" },
    ]
    for (const ds of scopedDatasources) {
      const attrs = (ds.attributes ?? {}) as Record<string, unknown>
      const rawTenantId = attrs.ob_tenant_id ?? attrs.tenant_id
      const tenantId =
        typeof rawTenantId === "number"
          ? rawTenantId
          : typeof rawTenantId === "string" && /^\d+$/.test(rawTenantId)
            ? Number(rawTenantId)
            : undefined
      const value = `datasource:${ds.id}`
      options.push({
        value,
        label: ds.name,
        tenantId,
      })
    }
    return options
  }, [scopedDatasources])

  const selectedDatasourceScopeParams = useMemo(() => {
    if (selectedDatasourceScope.startsWith("datasource:")) {
      const selected = datasourceOptions.find((item) => item.value === selectedDatasourceScope)
      if (!selected) return {}
      const params: { tenant_id?: number } = {}
      if (selected.tenantId != null) params.tenant_id = selected.tenantId
      return params
    }
    return {}
  }, [datasourceOptions, selectedDatasourceScope])

  const effectiveDatasourceId = useMemo(() => {
    if (selectedDatasourceScope.startsWith("datasource:")) {
      const parsed = Number(selectedDatasourceScope.slice("datasource:".length))
      return Number.isFinite(parsed) && parsed > 0 ? parsed : null
    }
    return null
  }, [selectedDatasourceScope])

  const effectiveClusterKey = useMemo(() => {
    if (selectedDatasourceScope !== "all") return null
    return selectedClusterKey === ALL_CLUSTERS ? null : selectedClusterKey
  }, [selectedClusterKey, selectedDatasourceScope])

  useEffect(() => {
    if (!clusterOptions.length) {
      setSelectedClusterKey(ALL_CLUSTERS)
      return
    }
    if (
      selectedClusterKey &&
      selectedClusterKey !== ALL_CLUSTERS &&
      !clusterOptions.includes(selectedClusterKey)
    ) {
      setSelectedClusterKey(ALL_CLUSTERS)
    }
  }, [clusterOptions, selectedClusterKey])

  useEffect(() => {
    if (!datasourceOptions.length) {
      setSelectedDatasourceScope("all")
      return
    }
    if (!datasourceOptions.some((item) => item.value === selectedDatasourceScope)) {
      setSelectedDatasourceScope("all")
    }
  }, [datasourceOptions, selectedDatasourceScope])


  const fetchData = useCallback(
    async (
      requestScope: { datasource_id?: number | null; cluster_key?: string | null; tenant_id?: number },
      silent = false
    ) => {
      if (!silent) {
        setSessionsLoading(true)
        setTxnsLoading(true)
      }
      setSessionsError(null)
      setTxnsError(null)

      const [sessionsResult, transactionsResult] = await Promise.allSettled([
        sessionAnalysisApi.listSessions(requestScope),
        sessionAnalysisApi.listTransactions(requestScope),
      ])

      let nextSessions: LiveSession[] = []
      let nextLongTxns: LiveTransaction[] = []
      let nextPendingTxns: LiveTransaction[] = []
      let nextTotal = 0
      let nextActive = 0

      if (sessionsResult.status === "fulfilled") {
        nextSessions = sessionsResult.value.sessions
        nextTotal = sessionsResult.value.total
        nextActive = sessionsResult.value.active
        setSessions(nextSessions)
        setSessionsTotal(nextTotal)
        setSessionsActive(nextActive)
      } else {
        setSessions([])
        setSessionsTotal(0)
        setSessionsActive(0)
        setSessionsError("会话查询失败")
      }

      if (transactionsResult.status === "fulfilled") {
        nextLongTxns = transactionsResult.value.long_transactions
        nextPendingTxns = transactionsResult.value.pending_transactions
        setLongTxns(nextLongTxns)
        setPendingTxns(nextPendingTxns)
      } else {
        setLongTxns([])
        setPendingTxns([])
        setTxnsError("事务查询失败")
      }


      setSessionsLoading(false)
      setTxnsLoading(false)
      setLastUpdated(new Date())
    },
    []
  )

  const requestScope = useMemo(
    () => ({
      datasource_id: effectiveDatasourceId,
      cluster_key: effectiveClusterKey,
      ...selectedDatasourceScopeParams,
    }),
    [effectiveClusterKey, effectiveDatasourceId, selectedDatasourceScopeParams]
  )

  useEffect(() => {
    if (selectedDatasourceId == null) return
    void fetchData(requestScope)
  }, [fetchData, requestScope, selectedDatasourceId])

  useEffect(() => {
    if (selectedDatasourceId == null || !autoRefreshEnabled) return
    const timer = window.setInterval(() => {
      void fetchData(requestScope, true)
    }, refreshIntervalMs)
    return () => window.clearInterval(timer)
  }, [autoRefreshEnabled, fetchData, refreshIntervalMs, requestScope, selectedDatasourceId])


  const filteredSessions = useMemo(() => {
    const keyword = sessionKeyword.trim().toLowerCase()
    return sessions.filter((session) => {
      if (sessionScope === "active" && session.state !== "ACTIVE") return false
      if (!keyword) return true
      const haystack = [
        String(session.session_id),
        session.user,
        session.identity_label ?? "",
        session.tenant_name ?? "",
        session.client_ip ?? "",
        session.db ?? "",
        session.current_sql ?? "",
      ]
        .join(" ")
        .toLowerCase()
      return haystack.includes(keyword)
    })
  }, [sessionKeyword, sessionScope, sessions])

  useEffect(() => {
    setSessionPage(1)
  }, [selectedClusterKey, selectedDatasourceScope, sessionScope, sessionKeyword])

  useEffect(() => {
    setTxnPage(1)
  }, [selectedClusterKey, selectedDatasourceScope, txnTab])

  const pagedSessions = useMemo(() => {
    const start = (sessionPage - 1) * SESSION_PAGE_SIZE
    return filteredSessions.slice(start, start + SESSION_PAGE_SIZE)
  }, [filteredSessions, sessionPage])
  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(filteredSessions.length / SESSION_PAGE_SIZE))
    if (sessionPage > totalPages) {
      setSessionPage(totalPages)
    }
  }, [filteredSessions.length, sessionPage])

  const txnItems = useMemo(
    () => (txnTab === "long" ? longTxns : pendingTxns),
    [longTxns, pendingTxns, txnTab]
  )
  const pagedTxnItems = useMemo(() => {
    const start = (txnPage - 1) * TXN_PAGE_SIZE
    return txnItems.slice(start, start + TXN_PAGE_SIZE)
  }, [txnItems, txnPage])
  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(txnItems.length / TXN_PAGE_SIZE))
    if (txnPage > totalPages) {
      setTxnPage(totalPages)
    }
  }, [txnItems.length, txnPage])

  const topUsers = useMemo(() => {
    const entries: Record<string, number> = {}
    for (const session of filteredSessions) {
      const identity = sessionIdentity(session)
      entries[identity] = (entries[identity] ?? 0) + 1
    }
    return buildTopDistribution(entries)
  }, [filteredSessions])

  const topIps = useMemo(() => {
    const entries: Record<string, number> = {}
    for (const session of filteredSessions) {
      if (!session.client_ip) continue
      entries[session.client_ip] = (entries[session.client_ip] ?? 0) + 1
    }
    return buildTopDistribution(entries)
  }, [filteredSessions])

  const effectiveDatasource = useMemo(
    () => scopedDatasources.find((ds) => ds.id === effectiveDatasourceId) ?? null,
    [scopedDatasources, effectiveDatasourceId]
  )

  const chatFreshSessionKey = effectiveDatasourceId != null
    ? `session-tx-ds-${effectiveDatasourceId}`
    : effectiveClusterKey
      ? `session-tx-cluster-${effectiveClusterKey}`
      : selectedDatasourceId != null
        ? "session-tx-all-clusters"
        : null

  const chatFocusObject = useMemo((): Record<string, unknown> | null => {
    if (selectedDatasourceId == null) return null
    return {
      total_sessions: sessionsTotal,
      active_sessions: sessionsActive,
      long_transaction_count: longTxns.length,
      pending_transaction_count: pendingTxns.length,
      top_users: topUsers.map(([user, count]) => ({ user, count })),
      long_transactions: longTxns.slice(0, 5).map((txn) => ({
        trans_type: txn.trans_type,
        elapsed_seconds: txn.elapsed_seconds,
        sql_list: txn.sql_list.slice(0, 3),
      })),
    }
  }, [selectedDatasourceId, sessionsTotal, sessionsActive, longTxns, pendingTxns, topUsers])

  const sessionChatAdapter = useMemo<SceneBusinessAgentAdapter>(
    () => ({
      page: "session-transaction",
      sceneKey: "session_transaction",
      title: "会话与事务诊断",
      placeholder: "追问会话或事务问题，例如：有锁等待吗？",
      buildContext: () => ({
        datasource: effectiveDatasource
          ? {
              id: effectiveDatasource.id,
              name: effectiveDatasource.name,
              cluster_key: effectiveDatasource.cluster_key,
              tenant_role: effectiveDatasource.tenant_role,
            }
          : null,
        snapshot: chatFocusObject,
      }),
    }),
    [effectiveDatasource, chatFocusObject]
  )

  const handleOpenAiDrawer = useCallback(() => {
    setAiDrawerOpen(true)
    setChatSuggestedPrompt("分析当前会话与事务快照，识别关键风险，给出结论和建议。")
  }, [])

  const handleKillConfirm = useCallback(async () => {
    if (!killTarget) return
    setKilling(true)
    try {
      await sessionAnalysisApi.killSession(killTarget.datasourceId, killTarget.sessionId)
      toast.success(`会话 ${killTarget.sessionId} 已终止`)
      setKillTarget(null)
      await fetchData(requestScope, true)
    } catch {
      toast.error("终止失败，请重试")
    } finally {
      setKilling(false)
    }
  }, [fetchData, killTarget, requestScope])

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup className="flex-1 md:items-end">
        <Select
          value={selectedClusterKey}
          onValueChange={(value) => {
            setSelectedClusterKey(value)
            setSelectedDatasourceScope("all")
          }}
        >
          <SelectTrigger aria-label="集群筛选" className="w-full md:w-44 bg-card">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_CLUSTERS}>全部集群</SelectItem>
            {clusterOptions.map((clusterKey) => (
              <SelectItem key={clusterKey} value={clusterKey}>
                {clusterKey}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={selectedDatasourceScope} onValueChange={setSelectedDatasourceScope}>
          <SelectTrigger aria-label="数据源筛选" className="w-full md:w-56 bg-card">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {datasourceOptions.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="w-full md:max-w-sm">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={sessionKeyword}
              onChange={(event) => setSessionKeyword(event.target.value)}
              placeholder="筛选用户 / IP / DB / SQL"
              className="pl-9"
            />
          </div>
        </div>
      </FilterToolbarGroup>
      <FilterToolbarGroup className="justify-end md:items-end">
        <div className="inline-flex h-10 items-center gap-0 rounded-md border border-border bg-muted/20 px-1.5 shadow-sm">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 rounded-md"
            disabled={!effectiveDatasourceId || sessionsLoading || txnsLoading}
            onClick={() => effectiveDatasourceId && void fetchData(selectedDatasourceScopeParams)}
            title="立即刷新"
            aria-label="立即刷新"
          >
            <RefreshCw className={cn("size-4", (sessionsLoading || txnsLoading) && "animate-spin")} />
          </Button>
          <div className="mx-1 h-6 w-px bg-border" />
          <div className="flex items-center gap-2 rounded-md px-2 py-1">
            <Switch checked={autoRefreshEnabled} onCheckedChange={setAutoRefreshEnabled} aria-label="自动刷新开关" />
            <span className="select-none text-sm font-medium text-foreground">自动刷新</span>
            <Select
              value={String(refreshIntervalMs)}
              onValueChange={(value) => setRefreshIntervalMs(Number(value) || DEFAULT_REFRESH_INTERVAL_MS)}
              disabled={!autoRefreshEnabled}
            >
              <SelectTrigger
                aria-label="自动刷新频率"
                className={cn(
                  "h-7 w-[88px] border-0 px-2 text-sm shadow-none",
                  autoRefreshEnabled
                    ? "bg-primary text-primary-foreground hover:bg-primary/90"
                    : "bg-muted text-muted-foreground"
                )}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REFRESH_INTERVAL_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={String(option.value)}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  const overview = (
    <SessionTransactionOverview
      sessionsTotal={sessionsTotal}
      sessionsActive={sessionsActive}
      longTxnCount={longTxns.length}
      pendingTxnCount={pendingTxns.length}
      topUsers={topUsers}
      topIps={topIps}
      onOpenAiDrawer={handleOpenAiDrawer}
    />
  )

  const sessionTable = (
    <Card className="animate-in fade-in slide-in-from-bottom-1 duration-500 shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
        <div className="flex items-center gap-4">
          <h2 className="text-sm font-medium text-foreground">当前连接会话</h2>
          <Tabs value={sessionScope} onValueChange={(value) => setSessionScope(value as "active" | "all")} className="w-fit">
            <TabsList>
              <TabsTrigger value="active">活跃会话</TabsTrigger>
              <TabsTrigger value="all">全部会话</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {lastUpdated ? <span>最后更新 {lastUpdated.toLocaleTimeString()}</span> : null}
          <span>
            当前显示 {filteredSessions.length} / {sessions.length} 条
          </span>
        </div>
      </div>
      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>会话 ID</TableHead>
              <TableHead>用户</TableHead>
              <TableHead>租户</TableHead>
              <TableHead>来源 IP</TableHead>
              <TableHead>数据库</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>持续时间</TableHead>
              <TableHead className="max-w-xs">当前 SQL</TableHead>
              <TableHead className="w-16 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessionsLoading ? (
              <ListTableLoadingRows rowCount={6} columnCount={9} />
            ) : sessionsError ? (
              <TableRow>
                <TableCell colSpan={9} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <AlertTriangle className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">{sessionsError}</p>
                  </div>
                </TableCell>
              </TableRow>
            ) : filteredSessions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <Users className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">没有匹配的会话</p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              pagedSessions.map((session, index) => (
                <TableRow
                  key={session.session_id}
                  className="cursor-pointer transition-colors duration-150 hover:bg-muted/40"
                  style={{ animationDelay: `${index * 30}ms` }}
                >
                  <TableCell className="font-mono text-xs">{session.session_id}</TableCell>
                  <TableCell className="font-medium">{sessionIdentity(session)}</TableCell>
                  <TableCell className="text-muted-foreground">{session.tenant_name ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{session.client_ip ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{session.db ?? "—"}</TableCell>
                  <TableCell>
                    <SessionBadge state={session.state} />
                  </TableCell>
                  <TableCell className="tabular-nums">{fmtSeconds(session.time_seconds)}</TableCell>
                  <TableCell className="max-w-xs truncate font-mono text-xs text-muted-foreground">
                    {session.current_sql ?? "—"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-negative hover:text-negative"
                      onClick={() =>
                        setKillTarget({
                          datasourceId: session.datasource_id,
                          sessionId: session.session_id,
                          currentSql: session.current_sql,
                        })
                      }
                    >
                      Kill
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
        <PaginationFooter
          page={sessionPage}
          pageSize={SESSION_PAGE_SIZE}
          total={filteredSessions.length}
          onPageChange={setSessionPage}
        />
      </ListTable>
    </Card>
  )

  const transactionTable = (items: LiveTransaction[], emptyText: string) => (
    <ListTable className="overflow-hidden border-0 rounded-none">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>事务 ID</TableHead>
            <TableHead>会话 ID</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>耗时</TableHead>
            <TableHead>SQL 样本</TableHead>
            <TableHead className="w-24 text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {txnsLoading ? (
            <ListTableLoadingRows rowCount={4} columnCount={6} />
          ) : txnsError ? (
            <TableRow>
              <TableCell colSpan={6} className="h-32 text-center">
                <div className="flex flex-col items-center gap-2">
                  <AlertTriangle className="size-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">{txnsError}</p>
                </div>
              </TableCell>
            </TableRow>
          ) : txnItems.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="h-32 text-center">
                <div className="flex flex-col items-center gap-2">
                  <Zap className="size-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">{emptyText}</p>
                </div>
              </TableCell>
            </TableRow>
          ) : (
            items.map((txn, index) => (
              <TableRow
                key={txn.trans_hash}
                className={cn(
                  "cursor-pointer transition-colors duration-150 hover:bg-muted/40",
                  drawerOpen && drawerTxn?.trans_hash === txn.trans_hash && "bg-primary/[0.04]"
                )}
                style={{ animationDelay: `${index * 30}ms` }}
                onClick={() => {
                  setDrawerTxn(txn)
                  setDrawerOpen(true)
                }}
              >
                <TableCell className="font-mono text-xs">{txn.trans_hash.slice(0, 16)}…</TableCell>
                <TableCell className="font-mono text-xs">{txn.session_id ?? "—"}</TableCell>
                <TableCell>
                  <TxnBadge state={txn.state} />
                </TableCell>
                <TableCell className="tabular-nums font-medium">{fmtSeconds(txn.elapsed_seconds)}</TableCell>
                <TableCell className="max-w-xs truncate font-mono text-xs text-muted-foreground">
                  {txn.sql_list[0] ?? "暂无 SQL 样本"}
                </TableCell>
                <TableCell className="text-right">
                  {txn.session_id ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-negative hover:text-negative"
                      onClick={(event) => {
                        event.stopPropagation()
                        setKillTarget({ datasourceId: txn.datasource_id, sessionId: txn.session_id!, currentSql: txn.sql_list[0] ?? null })
                      }}
                    >
                      Kill
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
      <PaginationFooter
        page={txnPage}
        pageSize={TXN_PAGE_SIZE}
        total={txnItems.length}
        onPageChange={setTxnPage}
      />
    </ListTable>
  )

  const transactionSection = (
    <Card className="animate-in fade-in slide-in-from-bottom-1 duration-500 shadow-sm" aria-label="事务表格区">
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
        <div>
          <h2 className="text-sm font-medium text-foreground">长事务与未提交事务</h2>
          <p className="mt-1 text-xs text-muted-foreground">按事务状态查看风险对象，点击任一事务可展开 SQL 样本与 AI 解读。</p>
        </div>
        <Tabs value={txnTab} onValueChange={(value) => setTxnTab(value as "long" | "pending")} className="w-fit">
          <TabsList>
            <TabsTrigger value="long">长事务</TabsTrigger>
            <TabsTrigger value="pending">未提交事务</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      {txnTab === "long"
        ? transactionTable(pagedTxnItems, "暂无长事务（> 60s）")
        : transactionTable(pagedTxnItems, "暂无未提交事务")}
    </Card>
  )

  const primary = datasourcesLoading ? (
    <Card>
      <CardContent className="flex min-h-40 items-center justify-center p-8 text-sm text-muted-foreground">
        正在加载会话诊断数据源…
      </CardContent>
    </Card>
  ) : datasourcesError ? (
    <Card>
      <CardContent className="flex min-h-40 flex-col items-center justify-center gap-3 p-8 text-sm text-muted-foreground">
        <AlertTriangle className="size-8 text-muted-foreground/40" />
        <div>{datasourcesError}</div>
        <Button variant="outline" size="sm" onClick={() => void loadDatasources()}>
          重试加载数据源
        </Button>
      </CardContent>
    </Card>
  ) : selectedDatasourceId == null ? (
    <Card>
      <CardContent className="flex min-h-40 flex-col items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
        <Users className="size-8 text-muted-foreground/40" />
        <div>当前集群没有可执行的会话诊断数据源。</div>
        <div className="text-xs text-muted-foreground">请先为该集群配置可用执行源，再返回本页查看会话与事务快照。</div>
      </CardContent>
    </Card>
  ) : (
    <div className="space-y-6">
      {overview}
      {sessionTable}
      {transactionSection}
    </div>
  )

  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />

      <KillConfirmDialog
        open={killTarget !== null}
        sessionId={killTarget?.sessionId ?? null}
        currentSql={killTarget?.currentSql ?? null}
        onConfirm={handleKillConfirm}
        onCancel={() => setKillTarget(null)}
        loading={killing}
      />

      <AiAnalysisDrawer
        open={aiDrawerOpen}
        onOpenChange={setAiDrawerOpen}
        adapter={sessionChatAdapter}
        datasourceId={effectiveDatasourceId}
        focusObject={chatFocusObject}
        freshSessionKey={chatFreshSessionKey}
        suggestedPrompt={chatSuggestedPrompt}
        onSuggestedPromptApplied={() => setChatSuggestedPrompt(null)}
      />

      <TransactionDrawer
        open={drawerOpen}
        txn={drawerTxn}
        onOpenChange={setDrawerOpen}
        onKill={(sessionId) => {
          setDrawerOpen(false)
          setKillTarget({ datasourceId: drawerTxn?.datasource_id ?? effectiveDatasourceId ?? 0, sessionId, currentSql: drawerTxn?.sql_list[0] ?? null })
        }}
      />
    </>
  )
}
