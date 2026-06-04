import { useEffect, useMemo, useState } from "react"
import { isAxiosError } from "axios"
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Code2,
  Loader2,
  Minus,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  XCircle,
} from "lucide-react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"

import { useShellI18n, type ShellTranslatorFn } from "@/i18n/shellI18n"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Drawer,
  DrawerBody,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { CodeBlock } from "@/components/shared/CodeBlock"
import { functionsApi, datasourcesApi, type DataSource } from "@/lib/api"

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }
type JsonObject = Record<string, JsonValue>

type FunctionInvokeMeta = {
  mode?: string
  requires_confirmation?: boolean
  result_mode?: string
}

type FunctionListItem = {
  id: number
  name?: string
  slug?: string
  description?: string
  kind?: string
  status?: string
  updated_at?: string
  draft_dependencies?: {
    invoke?: FunctionInvokeMeta | null
  } | null
}

type FunctionRunItem = {
  id: number
  run_id?: string
  function_id?: number
  function_name?: string
  function_slug?: string
  status?: string
  duration_ms?: number
  input_summary?: string
  output_summary?: string
  error_class?: string
  error_message?: string
  started_at?: string
  finished_at?: string
  created_at?: string
}

type InputRow = { id: string; key: string; value: string }

type InvokeResponse = {
  status?: string
  duration_ms?: number
  run_id?: string
  output?: JsonValue
  error_message?: string
  error_class?: string
  error_code?: string
  runtime_path?: string
}

function toFriendlyInvokeError(t: ShellTranslatorFn, message: string, errorCode?: string): string {
  const raw = String(message || "").trim()
  const byCode: Record<string, string> = {
    release_required: t("fn.error.releaseRequired"),
    datasource_required: t("fn.error.datasourceRequired"),
    sql_param_placeholder: t("fn.error.sqlParamPlaceholder"),
    sql_syntax_error: t("fn.error.sqlSyntaxError"),
    sql_object_not_found: t("fn.error.sqlObjectNotFound"),
  }
  const code = String(errorCode || "").trim()
  if (code && byCode[code]) return byCode[code]
  return raw || t("fn.error.invokeFallback")
}

function formatJsonValue(value: JsonValue | null | undefined): string {
  if (value == null) return ""
  return JSON.stringify(value, null, 2)
}

function formatInvokeErrorPayload(value: unknown, fallback: string): string {
  if (typeof value === "string") {
    const trimmed = value.trim()
    return trimmed || fallback
  }
  if (value && typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return fallback
    }
  }
  return fallback
}

function resolveInvokeConfig(item: FunctionListItem | null): { executionMode: "plan" | "apply"; writeMode: "readonly" | "write"; requiresConfirmation: boolean } {
  const invokeMeta = item?.draft_dependencies?.invoke
  const isReleased = item?.status === "released" || item?.status === "published"
  if (invokeMeta?.mode === "write_apply") {
    return {
      executionMode: "apply",
      writeMode: "write",
      requiresConfirmation: invokeMeta.requires_confirmation !== false,
    }
  }
  // Released functions run in apply mode — they have passed review and should execute normally.
  if (isReleased) {
    return {
      executionMode: "apply",
      writeMode: "readonly",
      requiresConfirmation: false,
    }
  }
  return {
    executionMode: "plan",
    writeMode: "readonly",
    requiresConfirmation: false,
  }
}

const PAGE_SIZE = 10

function getStatusMap(t: ShellTranslatorFn): Record<string, { label: string; variant: "default" | "secondary" | "outline" }> {
  return {
    draft: { label: t("fn.status.draft"), variant: "outline" },
    published: { label: t("fn.status.published"), variant: "default" },
    released: { label: t("fn.status.published"), variant: "default" },
    archived: { label: t("fn.status.archived"), variant: "secondary" },
  }
}

const RUN_STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Loader2 className="size-3.5 animate-spin text-blue-500" />,
  success: <CheckCircle2 className="size-3.5 text-emerald-500" />,
  failed: <XCircle className="size-3.5 text-destructive" />,
}

function payloadFromRows(rows: InputRow[]): JsonObject {
  const result: JsonObject = {}
  rows.forEach((row) => {
    const key = row.key.trim()
    if (!key) return
    const raw = row.value.trim()
    if (!raw) {
      result[key] = ""
      return
    }
    try {
      result[key] = JSON.parse(raw)
    } catch {
      result[key] = raw
    }
  })
  return result
}

export function FunctionListPage() {
  const { t } = useShellI18n()
  const navigate = useNavigate()

  const [activeTab, setActiveTab] = useState<"list" | "history">("list")
  const [functions, setFunctions] = useState<FunctionListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [deleteTarget, setDeleteTarget] = useState<FunctionListItem | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)

  const [runs, setRuns] = useState<FunctionRunItem[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [runsPage, setRunsPage] = useState(1)

  const [invokeTarget, setInvokeTarget] = useState<FunctionListItem | null>(null)
  const [inputRows, setInputRows] = useState<InputRow[]>([{ id: "row-initial", key: "", value: "" }])
  const [suggestingInput, setSuggestingInput] = useState(false)
  const [invoking, setInvoking] = useState(false)
  const [invokeOutput, setInvokeOutput] = useState<JsonValue | null>(null)
  const [invokeError, setInvokeError] = useState("")
  const [invokeMeta, setInvokeMeta] = useState<{ status?: string; durationMs?: number; runId?: string } | null>(null)
  const [drawerPayload, setDrawerPayload] = useState<JsonObject | null>(null)
  const [runDrawerOpen, setRunDrawerOpen] = useState(false)
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [invokeSelectedDatasource, setInvokeSelectedDatasource] = useState<number | null>(null)

  const fetchList = (showRefresh = false) => {
    if (showRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)
    functionsApi.list()
      .then((data) => setFunctions(Array.isArray(data) ? data : []))
      .catch(() => {
        if (!showRefresh) setError(t("fn.loadFailed"))
        else toast.error(t("fn.refreshFailed"))
      })
      .finally(() => {
        setLoading(false)
        setRefreshing(false)
      })
  }

  const fetchRuns = () => {
    setRunsLoading(true)
    setRunsError(null)
    functionsApi.listAllRuns(200)
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch(() => setRunsError(t("fn.runsLoadFailed")))
      .finally(() => setRunsLoading(false))
  }

  useEffect(() => {
    fetchList()
  }, [])

  useEffect(() => {
    if (activeTab === "history") fetchRuns()
  }, [activeTab])

  const visibleFunctions = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return functions
    return functions.filter((item) =>
      `${item.id} ${item.name || ""} ${item.slug || ""} ${item.description || ""}`.toLowerCase().includes(keyword)
    )
  }, [functions, search])

  const pagedFunctions = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return visibleFunctions.slice(start, start + PAGE_SIZE)
  }, [visibleFunctions, page])

  const pagedRuns = useMemo(() => {
    const start = (runsPage - 1) * PAGE_SIZE
    return runs.slice(start, start + PAGE_SIZE)
  }, [runs, runsPage])

  useEffect(() => {
    setPage(1)
  }, [search])

  const handleCreateFunction = async () => {
    if (creating) return
    setCreating(true)
    try {
      const created = await functionsApi.create({})
      setFunctions((prev) => [created, ...prev])
      navigate(`/function/${created.id}/build`)
    } catch (err) {
      const detail = isAxiosError(err)
        ? String((err.response?.data as any)?.detail || "")
        : ""
      toast.error(detail || t("fn.createFailed"))
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setBusyAction(`delete:${deleteTarget.id}`)
    try {
      await functionsApi.delete(deleteTarget.id)
      setFunctions((prev) => prev.filter((item) => item.id !== deleteTarget.id))
      setDeleteTarget(null)
      toast.success(t("fn.deleted"))
    } catch (err) {
      const detail = isAxiosError(err)
        ? String((err.response?.data as any)?.detail || "")
        : ""
      toast.error(detail || t("fn.deleteFailed"))
    } finally {
      setBusyAction(null)
    }
  }

  const openInvokeDialog = async (item: FunctionListItem) => {
    setInputRows([{ id: `row-${Date.now()}`, key: "", value: "" }])
    setInvokeOutput(null)
    setInvokeError("")
    setInvokeMeta(null)
    setInvokeSelectedDatasource(null)
    datasourcesApi.list().then((list) => setDatasources(Array.isArray(list) ? list : [])).catch(() => {})
    try {
      const detail = await functionsApi.get(item.id)
      setInvokeTarget(detail)
      const code: string = detail?.draft_code || ""
      const matches = [...code.matchAll(/payload\.get\(\s*["'](\w+)["'](?:\s*,\s*([^)]+))?\)/g)]
      if (matches.length > 0) {
        setInputRows(
          matches.map((m, idx) => {
            const key = m[1]
            const raw = (m[2] || "").trim()
            return { id: `row-${Date.now()}-${idx}`, key, value: raw }
          })
        )
      }
    } catch {
      setInvokeTarget(item)
      // ignore — keep empty rows as fallback
    }
  }

  const handleSuggestInput = async () => {
    if (!invokeTarget || suggestingInput) return
    setSuggestingInput(true)
    try {
      const res = await functionsApi.suggestInput(invokeTarget.id, {})
      const suggestion = res?.suggestion || res
      if (suggestion?.payload && typeof suggestion.payload === "object") {
        const entries = Object.entries(suggestion.payload as Record<string, unknown>)
        if (entries.length > 0) {
          setInputRows(
            entries.map(([key, value], idx) => ({
              id: `row-${Date.now()}-${idx}`,
              key,
              value: typeof value === "string" ? value : JSON.stringify(value),
            }))
          )
          toast.success(t("fn.suggestInputSuccess"))
          return
        }
      }
      toast.info(t("fn.suggestInputEmpty"))
    } catch {
      toast.error(t("fn.suggestInputFailed"))
    } finally {
      setSuggestingInput(false)
    }
  }

  const handleInvoke = async () => {
    if (!invokeTarget || invoking) return
    const payload = payloadFromRows(inputRows)
    const invokeConfig = resolveInvokeConfig(invokeTarget)
    setDrawerPayload(payload)
    setInvoking(true)
    setInvokeOutput(null)
    setInvokeError("")
    setInvokeMeta(null)
    setInvokeTarget(null)
    setActiveTab("history")
    setRunDrawerOpen(true)

    try {
      const res: InvokeResponse = await functionsApi.invoke(invokeTarget.id, {
        payload,
        write_mode: invokeConfig.writeMode,
        execution_mode: invokeConfig.executionMode,
        runtime_path: "production",
        ...(invokeSelectedDatasource != null ? { datasource_id: invokeSelectedDatasource } : {}),
        ...(invokeConfig.requiresConfirmation ? { confirm_apply: true } : {}),
      })
      setInvokeOutput(res.output ?? null)
      setInvokeMeta({
        status: res.status,
        durationMs: res.duration_ms,
        runId: res.run_id,
      })
      if (res.error_message) {
        setInvokeError(toFriendlyInvokeError(t, res.error_message, res.error_code))
      }
      fetchRuns()
    } catch (err) {
      const errObj = err && typeof err === "object"
        ? (err as { response?: { data?: { detail?: unknown } }; message?: unknown })
        : null
      const detailPayload = errObj?.response?.data?.detail ?? ""
      const rawDetail = typeof detailPayload === "string"
        ? detailPayload
        : (detailPayload && typeof detailPayload === "object"
          ? String((detailPayload as { message?: unknown }).message || "")
          : "")
      const rawCode = detailPayload && typeof detailPayload === "object"
        ? String((detailPayload as { error_code?: unknown }).error_code || "")
        : ""
      const fallbackDetail = String(errObj?.message || "")
      const friendly = toFriendlyInvokeError(t, rawDetail || fallbackDetail || t("fn.error.executionFailed"), rawCode)
      const formatted = detailPayload && typeof detailPayload === "object"
        ? formatInvokeErrorPayload(detailPayload, friendly)
        : friendly
      setInvokeError(formatted)
      setInvokeMeta({ status: "failed" })
    } finally {
      setInvoking(false)
    }
  }

  const toolbar = (
    <div className="rounded-xl bg-card p-4 shadow-sm">
      <FilterToolbar>
        <FilterToolbarGroup>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-72 rounded-lg bg-card pl-9 text-sm"
              placeholder={t("fn.searchPlaceholder")}
            />
          </div>
        </FilterToolbarGroup>
        <FilterToolbarGroup>
          <Button variant="outline" size="sm" onClick={() => {
            if (activeTab === "list") fetchList(true)
            else fetchRuns()
          }} disabled={refreshing || runsLoading}>
            {(refreshing || runsLoading) ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            {t("fn.refresh")}
          </Button>
          {activeTab === "list" ? (
            <Button size="sm" onClick={handleCreateFunction} disabled={creating}>
              {creating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              {t("fn.create")}
            </Button>
          ) : null}
        </FilterToolbarGroup>
      </FilterToolbar>
    </div>
  )

  const functionListContent = (
    <ListTable className="border-0 rounded-none">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16">ID</TableHead>
            <TableHead className="w-[240px]">{t("fn.col.name")}</TableHead>
            <TableHead className="w-20">{t("fn.col.kind")}</TableHead>
            <TableHead className="w-24">{t("fn.col.status")}</TableHead>
            <TableHead>{t("fn.col.description")}</TableHead>
            <TableHead className="w-40">{t("fn.col.updatedAt")}</TableHead>
            <TableHead className="w-28 text-right">{t("fn.col.actions")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <ListTableLoadingRows rowCount={6} columnCount={7} />
          ) : error ? (
            <TableRow>
              <TableCell colSpan={7} className="h-32 text-center">
                <div className="flex flex-col items-center gap-2">
                  <AlertTriangle className="size-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">{error}</p>
                  <Button variant="ghost" size="sm" onClick={() => fetchList()}>
                    {t("fn.retry")}
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ) : pagedFunctions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} className="h-32 text-center">
                <div className="flex flex-col items-center gap-2">
                  <Code2 className="size-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">
                    {search ? t("fn.emptyNoMatch") : t("fn.emptyNone")}
                  </p>
                  {search ? (
                    <Button variant="ghost" size="sm" onClick={() => setSearch("")}>
                      {t("fn.clearSearch")}
                    </Button>
                  ) : (
                    <Button variant="ghost" size="sm" onClick={handleCreateFunction} disabled={creating}>
                      <Plus className="size-4" />
                      {t("fn.createFunction")}
                    </Button>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ) : (
            pagedFunctions.map((item, index) => {
              const isBuiltIn = item.kind === "built_in" || item.kind === "builtin"
              const canExecute = isBuiltIn || item.status === "published" || item.status === "released"
              const statusMap = getStatusMap(t)
              const status = statusMap[item.status || "draft"] || statusMap.draft
              return (
                <TableRow
                  key={item.id}
                  style={{ animationDelay: `${index * 30}ms` }}
                  className="cursor-pointer transition-colors duration-150 hover:bg-muted/40"
                  onClick={() => navigate(`/function/${item.id}/build`)}
                >
                  <TableCell className="font-mono text-xs text-muted-foreground tabular-nums">
                    #{item.id}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                        <Code2 className="size-3.5 text-muted-foreground" />
                      </div>
                      <div className="min-w-0">
                        <span className="font-medium text-foreground">{item.name || `Function ${item.id}`}</span>
                        {item.slug ? (
                          <p className="truncate text-xs text-muted-foreground">#{item.id} · {item.slug}</p>
                        ) : null}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={isBuiltIn ? "secondary" : "outline"}
                      className="text-[11px]"
                    >
                      {isBuiltIn ? t("shared.term.builtIn") : t("shared.term.custom")}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={status.variant} className="text-[11px]">{status.label}</Badge>
                  </TableCell>
                  <TableCell className="max-w-[400px] truncate text-muted-foreground" title={item.description || "-"}>
                    {item.description || "-"}
                  </TableCell>
                  <TableCell className="text-xs tabular-nums text-muted-foreground" title={item.updated_at || "-"}>
                    {item.updated_at || "-"}
                  </TableCell>
                  <TableCell className="text-right">
                    <div
                      className="flex items-center justify-end gap-1"
                      role="group"
                      aria-label={`${t("fn.actionsAria")} ${item.name || `Function ${item.id}`}`}
                    >
                      {canExecute ? (
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          title={t("fn.invokeTitle")}
                          aria-label={`${t("fn.invokeAria")} ${item.name || `Function ${item.id}`}`}
                          onClick={(e) => {
                            e.stopPropagation()
                            openInvokeDialog(item)
                          }}
                        >
                          <Play className="size-3.5" />
                        </Button>
                      ) : null}
                      {isBuiltIn ? null : (
                        <>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={`${t("fn.editAria")} ${item.name || `Function ${item.id}`}`}
                            onClick={(e) => {
                              e.stopPropagation()
                              navigate(`/function/${item.id}/build`)
                            }}
                            disabled={Boolean(busyAction)}
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            className="text-destructive hover:text-destructive"
                            aria-label={`${t("fn.deleteAria")} ${item.name || `Function ${item.id}`}`}
                            onClick={(e) => {
                              e.stopPropagation()
                              setDeleteTarget(item)
                            }}
                            disabled={Boolean(busyAction)}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              )
            })
          )}
        </TableBody>
      </Table>
      {!loading && !error ? (
        <PaginationFooter
          page={page}
          pageSize={PAGE_SIZE}
          total={visibleFunctions.length}
          onPageChange={setPage}
          className="border-t border-border px-4 py-2"
        />
      ) : null}
    </ListTable>
  )

  const historyContent = (
    <ListTable className="border-0 rounded-none">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[200px]">{t("fn.historyCol.function")}</TableHead>
            <TableHead className="w-24">{t("fn.historyCol.status")}</TableHead>
            <TableHead className="w-24">{t("fn.historyCol.duration")}</TableHead>
            <TableHead>{t("fn.historyCol.inputSummary")}</TableHead>
            <TableHead className="w-44">{t("fn.historyCol.executionTime")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runsLoading ? (
            <ListTableLoadingRows rowCount={6} columnCount={5} />
          ) : runsError ? (
            <TableRow>
              <TableCell colSpan={5} className="h-32 text-center">
                <div className="flex flex-col items-center gap-2">
                  <AlertTriangle className="size-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">{runsError}</p>
                  <Button variant="ghost" size="sm" onClick={fetchRuns}>
                    {t("fn.retry")}
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ) : pagedRuns.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="h-32 text-center">
                <div className="flex flex-col items-center gap-2">
                  <Clock className="size-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">{t("fn.historyEmpty")}</p>
                </div>
              </TableCell>
            </TableRow>
          ) : (
            pagedRuns.map((run, index) => (
              <TableRow
                key={run.id}
                style={{ animationDelay: `${index * 30}ms` }}
                className="transition-colors duration-150 hover:bg-muted/40"
              >
                <TableCell>
                  <div className="min-w-0">
                    <span className="font-medium text-foreground">{run.function_name || `Function #${run.function_id}`}</span>
                    {run.function_slug ? (
                      <p className="truncate text-xs text-muted-foreground">{run.function_slug}</p>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-1.5">
                    {RUN_STATUS_ICON[run.status || ""] || <Minus className="size-3.5 text-muted-foreground" />}
                    <span className="text-xs">{run.status || "-"}</span>
                  </div>
                </TableCell>
                <TableCell className="text-xs tabular-nums text-muted-foreground">
                  {run.duration_ms != null ? `${run.duration_ms}ms` : "-"}
                </TableCell>
                <TableCell className="max-w-[300px] truncate text-xs text-muted-foreground" title={run.input_summary || "-"}>
                  {run.input_summary || "-"}
                </TableCell>
                <TableCell className="text-xs tabular-nums text-muted-foreground">
                  {run.started_at || run.created_at || "-"}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
      {!runsLoading && !runsError ? (
        <PaginationFooter
          page={runsPage}
          pageSize={PAGE_SIZE}
          total={runs.length}
          onPageChange={setRunsPage}
          className="border-t border-border px-4 py-2"
        />
      ) : null}
    </ListTable>
  )

  const primary = (
    <section className="animate-in fade-in slide-in-from-bottom-1 duration-500">
      <div className="overflow-hidden rounded-xl bg-card shadow-sm">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-4">
            <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as "list" | "history")} className="w-fit">
              <TabsList>
                <TabsTrigger value="list">
                  {t("fn.tab.list")}
                  {!loading && !error ? (
                    <span className="ml-1.5 rounded-full bg-muted px-1.5 text-[10px] tabular-nums">
                      {visibleFunctions.length}
                    </span>
                  ) : null}
                </TabsTrigger>
                <TabsTrigger value="history">
                  {t("fn.tab.history")}
                  {!runsLoading && runs.length > 0 ? (
                    <span className="ml-1.5 rounded-full bg-muted px-1.5 text-[10px] tabular-nums">
                      {runs.length}
                    </span>
                  ) : null}
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>
        {activeTab === "list" ? functionListContent : historyContent}
      </div>
    </section>
  )

  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />

      <ConfirmActionDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t("fn.deleteTitle")}
        description={
          <span className="space-y-1">
            <span className="block">
              {t("fn.deleteDesc.pre")} <span className="font-semibold text-foreground">{deleteTarget?.name || t("fn.deleteDesc.currentFunction")}</span>{" "}
              {t("fn.deleteDesc.post")}
            </span>
            {deleteTarget ? (
              <span className="block text-xs text-muted-foreground">
                {t("fn.deleteDesc.target")}#{deleteTarget.id}
                {deleteTarget.slug ? ` · ${deleteTarget.slug}` : ""}
              </span>
            ) : null}
          </span>
        }
        confirmText={t("fn.deleteConfirm")}
        confirming={busyAction?.startsWith("delete:") ?? false}
        confirmDisabled={!deleteTarget}
        onConfirm={handleDelete}
      />

      <Dialog open={Boolean(invokeTarget)} onOpenChange={(open) => !open && setInvokeTarget(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("fn.invokeDialogTitle")}</DialogTitle>
            <DialogDescription>
              {invokeTarget?.name || `Function #${invokeTarget?.id}`}
              {invokeTarget?.slug ? ` · ${invokeTarget.slug}` : ""}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            {datasources.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-sm font-medium text-foreground">{t("fn.datasource")}</p>
                <Select
                  value={invokeSelectedDatasource != null ? String(invokeSelectedDatasource) : ""}
                  onValueChange={(v) => setInvokeSelectedDatasource(v ? Number(v) : null)}
                >
                  <SelectTrigger className="w-full text-sm">
                    <SelectValue placeholder={t("fn.datasourceDefault")} />
                  </SelectTrigger>
                  <SelectContent>
                    {datasources.map((ds) => (
                      <SelectItem key={ds.id} value={String(ds.id)}>
                        {ds.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium text-foreground">{t("fn.inputParams")}</p>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={handleSuggestInput} disabled={suggestingInput}>
                  {suggestingInput ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  {t("fn.suggestInput")}
                </Button>
                <button
                  type="button"
                  aria-label={t("fn.addParamAria")}
                  onClick={() => setInputRows((prev) => [...prev, { id: `row-${Date.now()}`, key: "", value: "" }])}
                  className="inline-flex size-8 items-center justify-center rounded-full border border-border bg-card text-muted-foreground transition hover:text-foreground"
                >
                  <Plus className="size-3.5" />
                </button>
              </div>
            </div>

            <div className="max-h-[280px] overflow-auto">
              <div className="grid grid-cols-[140px_minmax(0,1fr)_32px] gap-2 px-1 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                <span>{t("fn.paramName")}</span>
                <span>{t("fn.paramValue")}</span>
                <span className="sr-only">{t("fn.paramActionsAria")}</span>
              </div>
              <div className="space-y-2">
                {inputRows.map((row) => (
                  <div key={row.id} className="grid grid-cols-[140px_minmax(0,1fr)_32px] items-center gap-2">
                    <Input
                      value={row.key}
                      placeholder={t("fn.paramKeyPlaceholder")}
                      className="text-sm"
                      onChange={(e) =>
                        setInputRows((prev) =>
                          prev.map((r) => (r.id === row.id ? { ...r, key: e.target.value } : r))
                        )
                      }
                    />
                    <Input
                      value={row.value}
                      placeholder={t("fn.paramValuePlaceholder")}
                      className="text-sm"
                      onChange={(e) =>
                        setInputRows((prev) =>
                          prev.map((r) => (r.id === row.id ? { ...r, value: e.target.value } : r))
                        )
                      }
                    />
                    <button
                      type="button"
                      aria-label={t("fn.deleteParamAria")}
                      onClick={() => setInputRows((prev) => prev.length > 1 ? prev.filter((r) => r.id !== row.id) : prev)}
                      className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition hover:text-destructive"
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setInvokeTarget(null)}>{t("fn.cancel")}</Button>
            <Button onClick={handleInvoke} disabled={invoking}>
              {invoking ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
              {t("fn.execute")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Drawer open={runDrawerOpen} onOpenChange={setRunDrawerOpen}>
        <DrawerContent className="flex max-w-[620px] flex-col bg-card text-foreground shadow-md">
          <DrawerHeader>
            <DrawerTitle>{t("fn.resultTitle")}</DrawerTitle>
          </DrawerHeader>

          <DrawerBody className="space-y-4">
            <CodeBlock label={t("fn.inputJson")} content={formatJsonValue(drawerPayload)} />

            <section>
              {invoking ? (
                <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/60 px-3 py-3 text-xs text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  {t("fn.executing")}
                </div>
              ) : invokeError ? (
                <CodeBlock label={t("fn.outputLabel")} content={invokeError} className="text-destructive" />
              ) : invokeOutput !== null ? (
                <CodeBlock label={t("fn.outputLabel")} content={formatJsonValue(invokeOutput)} />
              ) : (
                <div className="rounded-lg border border-border bg-muted/60 px-3 py-3 text-xs text-muted-foreground">
                  {t("fn.outputPlaceholder")}
                </div>
              )}
            </section>

            <div className="rounded-lg border border-border bg-muted/60 px-3 py-2.5 text-xs font-mono text-muted-foreground space-y-1">
              <p>{t("fn.metaStatus")} {invokeMeta?.status || (invoking ? "running" : "-")}</p>
              <p>{t("fn.metaDuration")} {invokeMeta?.durationMs ? `${invokeMeta.durationMs}ms` : "-"}</p>
              <p>Run ID: {invokeMeta?.runId || "-"}</p>
            </div>
          </DrawerBody>
        </DrawerContent>
      </Drawer>
    </>
  )
}
