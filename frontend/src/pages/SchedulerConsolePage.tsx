import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { toast } from "sonner"
import { useShellI18n } from "@/i18n/shellI18n"
import {
  AlertTriangle,
  Bot,
  Calendar,
  Loader2,
  PauseCircle,
  PlayCircle,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Sparkles,
  SquarePen,
  Trash2,
  X,
} from "lucide-react"

import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Drawer, DrawerBody, DrawerClose, DrawerContent, DrawerHeader, DrawerTitle } from "@/components/ui/drawer"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { CodeBlock } from "@/components/shared/CodeBlock"
import {
  agentsApi,
  datasourcesApi,
  filterConnectableDatasources,
  functionsApi,
  schedulesApi,
  type Agent,
  type DataSource,
  type Schedule,
  type ScheduleRun,
  type ScheduleTargetType as ApiScheduleTargetType,
} from "@/lib/api"

type ScheduleTargetType = ApiScheduleTargetType
type ScheduleType = "cron" | "interval"
type ScheduleStatus = "active" | "paused"

type ScheduleFormState = {
  name: string
  description: string
  target_type: ScheduleTargetType
  target_id: string
  schedule_type: ScheduleType
  cron_expression: string
  interval_seconds: string
  timezone: string
  datasource_id: string
  status: ScheduleStatus
  max_retries: string
  retry_backoff_seconds: string
  input_prompt: string
  input_payload_text: string
}

type FunctionContractField = {
  name: string
  type: string
  required: boolean
  description: string
}

type FunctionSummary = {
  id?: number
  name?: string
  status?: string
  description?: string
  draft_dependencies?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (isRecord(error) && typeof error.message === "string" && error.message.trim()) {
    return error.message
  }
  return fallback
}

const EMPTY_FORM: ScheduleFormState = {
  name: "",
  description: "",
  target_type: "function",
  target_id: "",
  schedule_type: "cron",
  cron_expression: "0 9 * * *",
  interval_seconds: "300",
  timezone: "Asia/Shanghai",
  datasource_id: "",
  status: "active",
  max_retries: "0",
  retry_backoff_seconds: "60",
  input_prompt: "",
  input_payload_text: "{}",
}

const SCHEDULE_PAGE_SIZE = 10
const RUN_PAGE_SIZE = 20

function toDisplayTime(value?: string | null): string {
  if (!value) return "-"
  const raw = String(value).trim()
  if (!raw) return "-"
  const normalized = raw.replace(" ", "T").replace(/\.(\d{3})\d+/, ".$1")
  const hasTimezone = /[zZ]$|[+-]\d{2}:\d{2}$/.test(normalized)
  const isoCandidate = hasTimezone ? normalized : `${normalized}Z`
  const date = new Date(isoCandidate)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function scheduleExpression(item: Schedule): string {
  if (item.schedule_type === "interval") {
    return `every ${item.interval_seconds ?? 0}s`
  }
  return item.cron_expression || "-"
}

function parsePayloadOrThrow(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const parsed = JSON.parse(trimmed)
  if (parsed === null) return null
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("input_payload must be a JSON object")
  }
  return parsed as Record<string, unknown>
}

function formatJsonLike(value: unknown, fallback = "-"): string {
  if (value === null || value === undefined) return fallback
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value ?? fallback)
  }
}

function describeRunStatus(run: ScheduleRun): string {
  const schedulerStatus = String(run.status || "").trim() || "-"
  const runtimeStatus = String(run.runtime_status || "").trim()
  if (!runtimeStatus || runtimeStatus === schedulerStatus) return schedulerStatus
  return `${schedulerStatus} · runtime=${runtimeStatus}`
}

function isRepairableRun(run: ScheduleRun | null): boolean {
  return String(run?.status || "").trim().toLowerCase() === "running"
}

function extractFunctionInputContract(fn: FunctionSummary | null): FunctionContractField[] {
  const draftDependencies = isRecord(fn?.draft_dependencies) ? fn.draft_dependencies : null
  const builderSpec = draftDependencies && isRecord(draftDependencies.builder_spec) ? draftDependencies.builder_spec : null
  const raw = builderSpec && Array.isArray(builderSpec.input_contract) ? builderSpec.input_contract : []
  return raw
    .filter((item): item is Record<string, unknown> => isRecord(item))
    .map((item) => ({
      name: String(item.name ?? "").trim(),
      type: String(item.type ?? "string").trim(),
      required: Boolean(item.required),
      description: String(item.description ?? "").trim(),
    }))
    .filter((item: FunctionContractField) => item.name.length > 0)
}

function resolveRunTargetMeta(
  run: ScheduleRun,
  scheduleById: Map<number, Schedule>,
  functionById: Map<number, FunctionSummary>,
  agentById: Map<number, Agent>,
  datasourceById: Map<number, DataSource>
): { scheduleName: string; scheduleId: number; type: string; name: string; id: number | null } {
  const schedule = scheduleById.get(Number(run.schedule_id))
  const scheduleId = Number(run.schedule_id || 0)
  const targetType = String(run.target_type || schedule?.target_type || "").toLowerCase()
  const targetId = Number(schedule?.target_id || schedule?.function_id || 0)
  if (targetType === "function") {
    const fn = functionById.get(targetId)
    return {
      scheduleName: schedule?.name || "-",
      scheduleId,
      type: "Function",
      name: fn?.name || "-",
      id: Number.isInteger(targetId) && targetId > 0 ? targetId : null,
    }
  }
  if (targetType === "agent") {
    const agent = agentById.get(targetId)
    return {
      scheduleName: schedule?.name || "-",
      scheduleId,
      type: "Agent",
      name: agent?.name || "-",
      id: Number.isInteger(targetId) && targetId > 0 ? targetId : null,
    }
  }
  if (targetType === "stats_analysis") {
    const datasource = datasourceById.get(targetId)
    return {
      scheduleName: schedule?.name || "-",
      scheduleId,
      type: "StatsAnalysis",
      name: datasource?.name || "-",
      id: Number.isInteger(targetId) && targetId > 0 ? targetId : null,
    }
  }
  return {
    scheduleName: schedule?.name || "-",
    scheduleId,
    type: targetType || "-",
    name: "-",
    id: Number.isInteger(targetId) && targetId > 0 ? targetId : null,
  }
}

export function SchedulerConsolePage() {
  const { t } = useShellI18n()
  const { schedulerId } = useParams()
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [view, setView] = useState<"schedules" | "runs">("schedules")
  const [search, setSearch] = useState("")
  const [schedulePage, setSchedulePage] = useState(1)
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [functions, setFunctions] = useState<FunctionSummary[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [runs, setRuns] = useState<ScheduleRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [runDrawerOpen, setRunDrawerOpen] = useState(false)
  const [runTypeFilter, setRunTypeFilter] = useState<"all" | "function" | "agent" | "stats_analysis">("all")
  const [runTargetNameFilter, setRunTargetNameFilter] = useState("")
  const [runScheduleFilter, setRunScheduleFilter] = useState<"all" | string>("all")
  const [runPage, setRunPage] = useState(1)
  const [runTotal, setRunTotal] = useState(0)

  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [editTargetId, setEditTargetId] = useState<number | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null)
  const [suggestingPayload, setSuggestingPayload] = useState(false)
  const [form, setForm] = useState<ScheduleFormState>(EMPTY_FORM)
  const [aiCreatePrompt, setAiCreatePrompt] = useState("")
  const [aiBuildPrompt, setAiBuildPrompt] = useState("")

  const selectedScheduleId = Number(schedulerId)
  const selectedSchedule = schedules.find((item) => item.id === selectedScheduleId) || null
  const editingSchedule = schedules.find((item) => item.id === editTargetId) || null
  const deleteTargetSchedule = schedules.find((item) => item.id === deleteTargetId) || null
  const selectedRun = runs.find((item) => item.id === selectedRunId) || runs[0] || null
  const scheduleById = useMemo(() => {
    const map = new Map<number, Schedule>()
    schedules.forEach((item) => {
      const id = Number(item?.id)
      if (Number.isInteger(id) && id > 0) map.set(id, item)
    })
    return map
  }, [schedules])
  const functionById = useMemo(() => {
    const map = new Map<number, FunctionSummary>()
    functions.forEach((item) => {
      const id = Number(item?.id)
      if (Number.isInteger(id) && id > 0) map.set(id, item)
    })
    return map
  }, [functions])
  const agentById = useMemo(() => {
    const map = new Map<number, Agent>()
    agents.forEach((item) => {
      const id = Number(item?.id)
      if (Number.isInteger(id) && id > 0) map.set(id, item)
    })
    return map
  }, [agents])
  const datasourceById = useMemo(() => {
    const map = new Map<number, DataSource>()
    datasources.forEach((item) => {
      const id = Number(item?.id)
      if (Number.isInteger(id) && id > 0) map.set(id, item)
    })
    return map
  }, [datasources])
  const selectedRunSchedule = useMemo(
    () => (selectedRun ? scheduleById.get(Number(selectedRun.schedule_id)) || null : null),
    [selectedRun, scheduleById]
  )
  const selectedRunTargetMeta = useMemo(() => {
    if (!selectedRun) return { scheduleName: "-", scheduleId: 0, type: "-", name: "-", id: null as number | null }
    return resolveRunTargetMeta(selectedRun, scheduleById, functionById, agentById, datasourceById)
  }, [selectedRun, scheduleById, functionById, agentById, datasourceById])
  const selectedRunOutputText = useMemo(
    () => formatJsonLike(selectedRun?.output_payload ?? selectedRun?.output_summary ?? selectedRun?.error_summary ?? "-", "-"),
    [selectedRun]
  )
  const selectedRunStatusText = useMemo(() => describeRunStatus(selectedRun || ({} as ScheduleRun)), [selectedRun])
  const selectedInputPayloadText = useMemo(
    () => formatJsonLike(selectedRunSchedule?.input_payload, "{}"),
    [selectedRunSchedule?.input_payload]
  )
  const selectedAgentPromptText = useMemo(
    () => String(selectedRunSchedule?.input_prompt || "").trim(),
    [selectedRunSchedule?.input_prompt]
  )
  const isSelectedAgentTarget = String(selectedRunSchedule?.target_type || "").toLowerCase() === "agent"

  const filteredRuns = useMemo(() => {
    const keyword = runTargetNameFilter.trim().toLowerCase()
    return runs.filter((run) => {
      if (runScheduleFilter !== "all" && String(run.schedule_id) !== runScheduleFilter) return false
      const meta = resolveRunTargetMeta(run, scheduleById, functionById, agentById, datasourceById)
      const runType = String(run.target_type || meta.type || "").toLowerCase()
      if (runTypeFilter !== "all" && runType !== runTypeFilter) return false
      if (keyword) {
        const searchable = `${run.run_id} ${meta.name} ${meta.scheduleName}`.toLowerCase()
        if (!searchable.includes(keyword)) return false
      }
      return true
    })
  }, [runs, runTypeFilter, runTargetNameFilter, runScheduleFilter, scheduleById, functionById, agentById, datasourceById])
  const visibleSchedules = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return schedules
    return schedules.filter((item) => {
      const text = `${item.name} ${item.target_type} ${item.schedule_type} ${item.status}`.toLowerCase()
      return text.includes(keyword)
    })
  }, [schedules, search])

  const pagedSchedules = useMemo(() => {
    const start = (schedulePage - 1) * SCHEDULE_PAGE_SIZE
    return visibleSchedules.slice(start, start + SCHEDULE_PAGE_SIZE)
  }, [schedulePage, visibleSchedules])

  useEffect(() => { setSchedulePage(1) }, [search])

  const releasedFunctions = useMemo(
    () => functions.filter((item) => String(item.status).toLowerCase() === "released"),
    [functions]
  )
  const activeAgents = useMemo(
    () => agents.filter((item) => String(item.status).toLowerCase() === "active"),
    [agents]
  )
  const activeDatasources = useMemo(
    () => datasources.filter((item) => String(item.status).toLowerCase() === "active"),
    [datasources]
  )
  const schedulableDatasources = useMemo(
    () => activeDatasources,
    [activeDatasources]
  )
  const selectedCreateFunction = releasedFunctions.find((item) => String(item.id) === form.target_id) || null
  const selectedFunctionContract = useMemo(
    () => extractFunctionInputContract(selectedCreateFunction),
    [selectedCreateFunction]
  )
  const editingIsBuiltIn = editingSchedule?.kind === "built_in"

  const refreshSchedules = async () => {
    setError(null)
    const [scheduleData, functionData, agentData, datasourceData] = await Promise.all([
      schedulesApi.list(),
      functionsApi.list(),
      agentsApi.list(),
      datasourcesApi.list(),
    ])
    setSchedules(Array.isArray(scheduleData) ? scheduleData : [])
    setFunctions(Array.isArray(functionData) ? functionData : [])
    setAgents(Array.isArray(agentData) ? agentData : [])
    setDatasources(filterConnectableDatasources(Array.isArray(datasourceData) ? datasourceData : []))
  }

  const refreshRuns = useCallback(async (page: number): Promise<ScheduleRun[]> => {
    const offset = Math.max(page - 1, 0) * RUN_PAGE_SIZE
    const normalizedScheduleId = runScheduleFilter === "all" ? undefined : Number(runScheduleFilter)
    const result = await schedulesApi.listAllRunsPage({
      limit: RUN_PAGE_SIZE,
      offset,
      schedule_id: Number.isInteger(normalizedScheduleId) && (normalizedScheduleId || 0) > 0 ? normalizedScheduleId : undefined,
    })
    const normalized = Array.isArray(result.items) ? result.items : []
    setRuns(normalized)
    setRunTotal(Math.max(result.total, 0))
    const totalPages = Math.max(1, Math.ceil(Math.max(result.total, 0) / RUN_PAGE_SIZE))
    if (page > totalPages) {
      setRunPage(totalPages)
    }
    setSelectedRunId((prev) => (prev && normalized.some((item) => item.id === prev) ? prev : normalized[0]?.id ?? null))
    return normalized
  }, [runScheduleFilter])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    refreshSchedules()
      .catch(() => {
        if (!cancelled) { setError(t("scheduler.toast.loadFailed")); toast.error(t("scheduler.toast.loadFailed")) }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (loading || schedules.length === 0) return
    if (!Number.isInteger(selectedScheduleId) || !schedules.some((item) => item.id === selectedScheduleId)) {
      navigate(`/scheduler/${schedules[0].id}`, { replace: true })
    }
  }, [loading, navigate, schedules, selectedScheduleId])

  useEffect(() => {
    refreshRuns(runPage).catch(() => toast.error(t("scheduler.toast.loadRunsFailed")))
  }, [refreshRuns, runPage])

  useEffect(() => {
    const hasRunningRun = runs.some((r) => r.status === "running")
    const interval = hasRunningRun ? 2000 : 10000
    const timer = window.setInterval(() => {
      refreshSchedules().catch(() => {})
      refreshRuns(runPage).catch(() => {})
    }, interval)
    return () => window.clearInterval(timer)
  }, [refreshRuns, runPage, runScheduleFilter, runs])

  const openCreateDialog = () => {
    const preferredFunctionId = releasedFunctions[0]?.id ? String(releasedFunctions[0].id) : ""
    setForm({
      ...EMPTY_FORM,
      target_type: "function",
      target_id: preferredFunctionId,
      schedule_type: "cron",
    })
    setAiCreatePrompt("")
    setCreateOpen(true)
  }

  const openEditDialog = (schedule: Schedule | null = selectedSchedule) => {
    if (!schedule) return
    setEditTargetId(schedule.id)
    setForm({
      name: schedule.name,
      description: schedule.description || "",
      target_type: (schedule.target_type || "function") as ScheduleTargetType,
      target_id: String(schedule.target_id || schedule.function_id || ""),
      schedule_type: schedule.schedule_type as ScheduleType,
      cron_expression: schedule.cron_expression || "0 9 * * *",
      interval_seconds: String(schedule.interval_seconds ?? 300),
      timezone: schedule.timezone || "Asia/Shanghai",
      datasource_id: schedule.datasource_id ? String(schedule.datasource_id) : "",
      status: (schedule.status || "active") as ScheduleStatus,
      max_retries: String(schedule.max_retries ?? 0),
      retry_backoff_seconds: String(schedule.retry_backoff_seconds ?? 60),
      input_prompt: schedule.input_prompt || "",
      input_payload_text: JSON.stringify(schedule.input_payload || {}, null, 2),
    })
    setAiBuildPrompt("")
    setEditOpen(true)
  }

  const openDeleteDialog = (scheduleId: number) => {
    setDeleteTargetId(scheduleId)
    setDeleteOpen(true)
  }

  const buildPayloadFromForm = (options?: { builtIn?: boolean }) => {
    const builtIn = Boolean(options?.builtIn)
    const timezone = form.timezone.trim() || "Asia/Shanghai"

    if (builtIn) {
      const payload: Record<string, unknown> = {
        status: form.status,
        schedule_type: form.schedule_type,
        timezone,
      }
      if (form.schedule_type === "cron") {
        payload.cron_expression = form.cron_expression.trim()
        payload.interval_seconds = null
      } else {
        payload.interval_seconds = Number(form.interval_seconds || "0")
        payload.cron_expression = null
      }
      return payload
    }

    const scheduleName = form.name.trim()
    if (!scheduleName) {
      throw new Error(t("scheduler.validate.nameRequired"))
    }
    const targetId = Number(form.target_id)
    if (!Number.isInteger(targetId) || targetId <= 0) {
      throw new Error(t("scheduler.validate.targetIdPositive"))
    }
    const payload: Record<string, unknown> = {
      name: scheduleName,
      description: form.description.trim() || undefined,
      target_type: form.target_type,
      target_id: targetId,
      schedule_type: form.schedule_type,
      timezone,
      datasource_id: form.datasource_id ? Number(form.datasource_id) : null,
      status: form.status,
      max_retries: Number(form.max_retries || "0"),
      retry_backoff_seconds: Number(form.retry_backoff_seconds || "60"),
      input_prompt: form.input_prompt.trim() || undefined,
      input_payload: parsePayloadOrThrow(form.input_payload_text),
    }
    if (form.schedule_type === "cron") {
      payload.cron_expression = form.cron_expression.trim()
      payload.interval_seconds = null
    } else {
      payload.interval_seconds = Number(form.interval_seconds || "0")
      payload.cron_expression = null
    }
    return payload
  }

  const handleCreateByAi = async () => {
    if (!aiCreatePrompt.trim()) {
      toast.error(t("scheduler.toast.aiDescRequired"))
      return
    }
    if (!form.name.trim()) {
      toast.error(t("scheduler.validate.nameRequired"))
      return
    }
    setBusyAction("ai-create")
    try {
      const targetId = Number(form.target_id)
      if (!Number.isInteger(targetId) || targetId <= 0) {
        throw new Error(t("scheduler.validate.targetIdPositive"))
      }
      const response = await schedulesApi.aiCreate({
        prompt: aiCreatePrompt.trim(),
        name: form.name.trim() || undefined,
        description: form.description.trim() || undefined,
        target_type: form.target_type,
        target_id: targetId,
        timezone: form.timezone.trim() || "Asia/Shanghai",
        datasource_id: form.datasource_id ? Number(form.datasource_id) : null,
        max_retries: Number(form.max_retries || "0"),
        retry_backoff_seconds: Number(form.retry_backoff_seconds || "60"),
        input_prompt: form.input_prompt.trim() || undefined,
        input_payload: parsePayloadOrThrow(form.input_payload_text),
      })
      await refreshSchedules()
      setRunPage(1)
      await refreshRuns(1)
      if (response?.schedule?.id) {
        navigate(`/scheduler/${response.schedule.id}`)
      }
      setCreateOpen(false)
      toast.success(response.build_summary || t("scheduler.toast.aiCreated"))
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, t("scheduler.toast.aiCreateFailed")))
    } finally {
      setBusyAction(null)
    }
  }

  const handleSuggestPayload = async () => {
    const functionId = Number(form.target_id)
    if (!Number.isInteger(functionId) || functionId <= 0) {
      toast.error(t("scheduler.toast.selectFunction"))
      return
    }
    setSuggestingPayload(true)
    try {
      const suggestion = await functionsApi.suggestInput(functionId, {
        prompt: aiCreatePrompt.trim() || t("scheduler.suggestPayload.defaultPrompt"),
      })
      if (suggestion?.payload && typeof suggestion.payload === "object" && !Array.isArray(suggestion.payload)) {
        setForm((prev) => ({ ...prev, input_payload_text: JSON.stringify(suggestion.payload, null, 2) }))
      }
      if (typeof suggestion?.rationale === "string" && suggestion.rationale.trim()) {
        toast.success(suggestion.rationale.trim())
      } else {
        toast.success(t("scheduler.toast.suggestPayloadOk"))
      }
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, t("scheduler.toast.suggestPayloadFailed")))
    } finally {
      setSuggestingPayload(false)
    }
  }

  const handleSaveEdit = async () => {
    if (!editingSchedule) return
    setBusyAction("save-edit")
    try {
      const updated = await schedulesApi.update(
        editingSchedule.id,
        buildPayloadFromForm({ builtIn: editingSchedule.kind === "built_in" })
      )
      setSchedules((prev) => prev.map((item) => (item.id === editingSchedule.id ? updated : item)))
      setEditOpen(false)
      setEditTargetId(null)
      toast.success(t("scheduler.toast.updated"))
      await refreshRuns(runPage)
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, t("scheduler.toast.updateFailed")))
    } finally {
      setBusyAction(null)
    }
  }

  const handleAiBuild = async () => {
    if (!editingSchedule || editingSchedule.kind === "built_in" || !aiBuildPrompt.trim()) return
    setBusyAction("ai-build")
    try {
      const response = await schedulesApi.build(editingSchedule.id, aiBuildPrompt.trim())
      setSchedules((prev) =>
        prev.map((item) => (item.id === editingSchedule.id ? response.schedule : item))
      )
      toast.success(response.build_summary || t("scheduler.toast.scheduleUpdated"))
      setAiBuildPrompt("")
      await refreshRuns(runPage)
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, t("scheduler.toast.aiBuildFailed")))
    } finally {
      setBusyAction(null)
    }
  }

  const handleDelete = async () => {
    const targetId = deleteTargetId ?? selectedSchedule?.id
    if (!targetId) return
    setBusyAction("delete")
    try {
      await schedulesApi.delete(targetId)
      setDeleteOpen(false)
      setDeleteTargetId(null)
      toast.success(t("scheduler.toast.deleted"))
      await refreshSchedules()
      await refreshRuns(runPage)
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, t("scheduler.toast.deleteFailed")))
    } finally {
      setBusyAction(null)
    }
  }

  const handleRunAction = async (action: "disable" | "enable" | "run-now", schedule: Schedule) => {
    setBusyAction(`${action}:${schedule.id}`)
    const triggeredRunId: string | null = null
    try {
      if (action === "disable") {
        const updated = await schedulesApi.disable(schedule.id)
        setSchedules((prev) => prev.map((item) => (item.id === schedule.id ? updated : item)))
      } else if (action === "enable") {
        const updated = await schedulesApi.enable(schedule.id)
        setSchedules((prev) => prev.map((item) => (item.id === schedule.id ? updated : item)))
      } else {
        const runResult = await schedulesApi.runNow(schedule.id)
        const scheduleRunId = runResult?.schedule_run_id ?? null
        toast.success(t("scheduler.toast.runNowTriggered"))
        // Open drawer immediately with the new run record
        if (scheduleRunId != null) {
          if (selectedSchedule?.id !== schedule.id) {
            navigate(`/scheduler/${schedule.id}`)
          }
          setSelectedRunId(scheduleRunId)
          setRunDrawerOpen(true)
          setRunPage(1)
          refreshRuns(1)
          await refreshSchedules()
          return
        }
      }
      await refreshSchedules()
      if (selectedSchedule?.id !== schedule.id) {
        navigate(`/scheduler/${schedule.id}`)
      }
      const targetPage = action === "run-now" ? 1 : runPage
      if (action === "run-now") setRunPage(1)
      const latestRuns = await refreshRuns(targetPage)
      if (action === "run-now" && latestRuns.length > 0) {
        const matched =
          latestRuns.find((item) => triggeredRunId && item.run_id === triggeredRunId) ||
          latestRuns.find((item) => item.schedule_id === schedule.id) ||
          latestRuns[0]
        if (matched) setSelectedRunId(matched.id)
        setRunDrawerOpen(true)
      }
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, `${t("scheduler.toast.actionFailed")}: ${action}`))
    } finally {
      setBusyAction(null)
    }
  }

  const handleRepairRun = async (run: ScheduleRun) => {
    const scheduleId = Number(run.schedule_id)
    if (!Number.isInteger(scheduleId) || scheduleId <= 0) {
      toast.error(t("scheduler.toast.missingScheduleId"))
      return
    }
    setBusyAction(`repair-run:${run.id}`)
    try {
      const repaired = await schedulesApi.repairRun(scheduleId, run.id)
      setRuns((prev) => prev.map((item) => (item.id === run.id ? repaired : item)))
      setSelectedRunId(repaired.id)
      toast.success(t("scheduler.toast.repairOk"))
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, t("scheduler.toast.repairFailed")))
    } finally {
      setBusyAction(null)
    }
  }

  const renderTargetItems = () => {
    if (form.target_type === "function") {
      if (releasedFunctions.length === 0) return null
      return releasedFunctions.map((item) => (
        <SelectItem key={item.id} value={String(item.id)}>#{item.id} {item.name}</SelectItem>
      ))
    }
    if (form.target_type === "agent") {
      if (activeAgents.length === 0) return null
      return activeAgents.map((item) => (
        <SelectItem key={item.id} value={String(item.id)}>#{item.id} {item.name}</SelectItem>
      ))
    }
    const options = schedulableDatasources
    if (options.length === 0) return null
    return options.map((item) => (
      <SelectItem key={item.id} value={String(item.id)}>#{item.id} {item.name}</SelectItem>
    ))
  }

  const targetPlaceholder = () => {
    if (form.target_type === "function" && releasedFunctions.length === 0) return t("scheduler.target.noAvailable")
    if (form.target_type === "agent" && activeAgents.length === 0) return t("scheduler.target.noAvailable")
    if ((form.target_type === "stats_analysis" || form.target_type === "collector") && schedulableDatasources.length === 0) return t("scheduler.target.noAvailable")
    return t("scheduler.target.selectTarget")
  }

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            value={view === "schedules" ? search : runTargetNameFilter}
            onChange={(e) => view === "schedules" ? setSearch(e.target.value) : setRunTargetNameFilter(e.target.value)}
            placeholder={view === "schedules" ? t("scheduler.search.schedules") : t("scheduler.search.runs")}
            className="w-72 rounded-lg bg-card pl-9 text-sm"
          />
        </div>
        {view === "runs" ? (
          <>
            <Select value={runScheduleFilter} onValueChange={(v) => { setRunScheduleFilter(v); setRunPage(1) }}>
              <SelectTrigger className="w-40 bg-card"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("scheduler.filter.allSchedules")}</SelectItem>
                {schedules.map((item) => (
                  <SelectItem key={item.id} value={String(item.id)}>{item.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={runTypeFilter} onValueChange={(v) => setRunTypeFilter(v as "all" | "function" | "agent" | "stats_analysis")}>
              <SelectTrigger className="w-36 bg-card"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("scheduler.filter.allTypes")}</SelectItem>
                <SelectItem value="function">Function</SelectItem>
                <SelectItem value="agent">Agent</SelectItem>
              </SelectContent>
            </Select>
          </>
        ) : null}
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        {view === "runs" ? (
          <Button variant="outline" size="sm" onClick={() => refreshRuns(runPage).catch(() => toast.error(t("scheduler.toast.refreshFailed")))}>
            <RefreshCw className="size-4" />
            {t("scheduler.btn.refresh")}
          </Button>
        ) : null}
        <Button size="sm" onClick={openCreateDialog}>
          <Plus className="size-4" />
          {t("scheduler.btn.create")}
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  const primary = (
    <section className="animate-in fade-in slide-in-from-bottom-1 duration-500">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="flex items-center gap-4">
          <Tabs value={view} onValueChange={(v) => setView(v as "schedules" | "runs")}>
            <TabsList>
              <TabsTrigger value="schedules">{t("scheduler.tab.schedules")}</TabsTrigger>
              <TabsTrigger value="runs">{t("scheduler.tab.runs")}</TabsTrigger>
            </TabsList>
          </Tabs>
          <span className="text-xs tabular-nums text-muted-foreground">
            {view === "schedules" ? `${visibleSchedules.length} ${t("scheduler.count.schedules")}` : `${runTotal} ${t("scheduler.count.runs")}`}
          </span>
        </div>
      </div>
      {view === "schedules" ? (
        <ListTable className="overflow-hidden border-0 rounded-none">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[220px]">{t("scheduler.col.name")}</TableHead>
                <TableHead className="w-[90px]">{t("scheduler.col.type")}</TableHead>
                <TableHead className="w-[160px]">{t("scheduler.col.target")}</TableHead>
                <TableHead className="w-[140px]">{t("scheduler.col.frequency")}</TableHead>
                <TableHead className="w-[100px]">{t("scheduler.col.status")}</TableHead>
                <TableHead className="w-[160px]">{t("scheduler.col.nextRun")}</TableHead>
                <TableHead className="w-28 text-right">{t("scheduler.col.actions")}</TableHead>
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
                      <Button variant="ghost" size="sm" onClick={() => { setLoading(true); setError(null); refreshSchedules().catch(() => setError(t("scheduler.toast.loadRetryFailed"))).finally(() => setLoading(false)) }}>
                        {t("scheduler.btn.retry")}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ) : pagedSchedules.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="h-32 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <Calendar className="size-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">
                        {search ? t("scheduler.empty.noMatch") : t("scheduler.empty.none")}
                      </p>
                      {search ? (
                        <Button variant="ghost" size="sm" onClick={() => setSearch("")}>{t("scheduler.btn.clearSearch")}</Button>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={openCreateDialog}>
                          <Plus className="size-4" />
                          {t("scheduler.btn.createScheduler")}
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                pagedSchedules.map((item, index) => {
                  const rowBusy = Boolean(busyAction && busyAction.endsWith(`:${item.id}`))
                  return (
                    <TableRow
                      style={{ animationDelay: `${index * 30}ms` }}
                      key={item.id}
                      className={`cursor-pointer transition-colors duration-150 hover:bg-muted/40 ${
                        selectedSchedule?.id === item.id ? "bg-primary/[0.04]" : ""
                      }`}
                      onClick={() => navigate(`/scheduler/${item.id}`)}
                    >
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                            <Calendar className="size-3.5 text-muted-foreground" />
                          </div>
                          <div className="min-w-0">
                            <span className="font-medium text-foreground">{item.name}</span>
                            <p className="text-xs text-muted-foreground">#{item.id}</p>
                            {item.description ? <p className="line-clamp-1 text-xs text-muted-foreground">{item.description}</p> : null}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={item.kind === "built_in" ? "secondary" : "outline"} className="text-[11px]">
                          {item.kind === "built_in" ? t("shared.term.builtIn") : t("shared.term.custom")}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[11px]">
                          {item.target_type} #{item.target_id || item.function_id || "-"}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{scheduleExpression(item)}</TableCell>
                      <TableCell>
                        <Badge variant={item.status === "active" ? "secondary" : "outline"} className="text-[11px]">{item.status}</Badge>
                      </TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">{toDisplayTime(item.next_run_at)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                          <Button aria-label={`${t("scheduler.action.runNow")} ${item.name}`} variant="ghost" size="icon-xs" onClick={() => handleRunAction("run-now", item)} disabled={rowBusy} title={t("scheduler.action.runNow")}>
                            <Rocket className="size-3.5" />
                          </Button>
                          <Button aria-label={`${item.status === "active" ? t("scheduler.action.pause") : t("scheduler.action.enable")} ${item.name}`} variant="ghost" size="icon-xs" onClick={() => handleRunAction(item.status === "active" ? "disable" : "enable", item)} disabled={rowBusy} title={item.status === "active" ? t("scheduler.action.pause") : t("scheduler.action.enable")}>
                            {item.status === "active" ? <PauseCircle className="size-3.5" /> : <PlayCircle className="size-3.5" />}
                          </Button>
                          <Button aria-label={`${t("scheduler.action.edit")} ${item.name}`} variant="ghost" size="icon-xs" onClick={() => openEditDialog(item)} disabled={rowBusy} title={item.kind === "built_in" ? t("scheduler.action.editTimingStatus") : t("scheduler.action.edit")}>
                            <SquarePen className="size-3.5" />
                          </Button>
                          <Button aria-label={`${t("scheduler.action.delete")} ${item.name}`} variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive" onClick={() => openDeleteDialog(item.id)} disabled={rowBusy || item.kind === "built_in"} title={item.kind === "built_in" ? t("scheduler.builtInDeleteDisabled") : t("scheduler.action.delete")}>
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
          {!loading && !error ? (
            <PaginationFooter page={schedulePage} pageSize={SCHEDULE_PAGE_SIZE} total={visibleSchedules.length} onPageChange={setSchedulePage} className="border-t border-border px-4 py-2" />
          ) : null}
        </ListTable>
      ) : (
        <ListTable className="overflow-hidden border-0 rounded-none">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[180px]">Schedule</TableHead>
                <TableHead className="w-[90px]">{t("scheduler.runs.col.type")}</TableHead>
                <TableHead className="w-[180px]">{t("scheduler.runs.col.runId")}</TableHead>
                <TableHead className="w-[90px]">{t("scheduler.runs.col.status")}</TableHead>
                <TableHead className="w-[80px]">{t("scheduler.runs.col.trigger")}</TableHead>
                <TableHead className="w-[60px]">{t("scheduler.runs.col.attempt")}</TableHead>
                <TableHead className="w-[140px]">{t("scheduler.runs.col.start")}</TableHead>
                <TableHead className="w-[140px]">{t("scheduler.runs.col.end")}</TableHead>
                <TableHead>{t("scheduler.runs.col.summary")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <ListTableLoadingRows rowCount={6} columnCount={9} />
              ) : filteredRuns.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="h-32 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <Calendar className="size-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">
                        {runTargetNameFilter || runScheduleFilter !== "all" || runTypeFilter !== "all" ? t("scheduler.runs.empty.filtered") : t("scheduler.runs.empty.none")}
                      </p>
                      {runTargetNameFilter ? (
                        <Button variant="ghost" size="sm" onClick={() => setRunTargetNameFilter("")}>{t("scheduler.btn.clearSearch")}</Button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                filteredRuns.map((run, index) => {
                  const meta = resolveRunTargetMeta(run, scheduleById, functionById, agentById, datasourceById)
                  return (
                    <TableRow
                      key={run.id}
                      style={{ animationDelay: `${index * 30}ms` }}
                      className={`cursor-pointer transition-colors duration-150 hover:bg-muted/40 ${
                        selectedRun?.id === run.id && runDrawerOpen ? "bg-primary/[0.04]" : ""
                      }`}
                      onClick={() => { setSelectedRunId(run.id); setRunDrawerOpen(true) }}
                    >
                      <TableCell className="max-w-[180px] truncate text-xs text-muted-foreground">
                        {meta.scheduleName} <span className="text-muted-foreground/60">#{meta.scheduleId}</span>
                      </TableCell>
                      <TableCell><Badge variant="outline" className="text-[11px]">{meta.type}</Badge></TableCell>
                      <TableCell className="max-w-[180px] truncate font-mono text-[11px] text-muted-foreground">{run.run_id}</TableCell>
                      <TableCell>
                        <Badge variant={run.status === "success" ? "secondary" : run.status === "failed" ? "outline" : "default"} className="text-[11px]" title={selectedRun?.id === run.id ? selectedRunStatusText : describeRunStatus(run)}>{describeRunStatus(run)}</Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{run.trigger_type}</TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">{run.attempt}/{Math.max((run.max_retries ?? 0) + 1, 1)}</TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">{toDisplayTime(run.started_at)}</TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">{toDisplayTime(run.finished_at)}</TableCell>
                      <TableCell className="max-w-[280px] truncate text-xs text-muted-foreground">{run.error_summary || run.output_summary || "-"}</TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
          {!loading ? (
            <PaginationFooter page={runPage} pageSize={RUN_PAGE_SIZE} total={runTotal} onPageChange={setRunPage} className="border-t border-border px-4 py-2" />
          ) : null}
        </ListTable>
      )}
    </section>
  )

  return (
    <>
      <WorkbenchPage
        toolbar={<div className="rounded-xl bg-card p-4 shadow-sm">{toolbar}</div>}
        primary={<div className="rounded-xl bg-card shadow-sm">{primary}</div>}
      />

      <Drawer open={runDrawerOpen} onOpenChange={setRunDrawerOpen}>
        <DrawerContent className="flex max-w-[560px] flex-col border-border bg-card shadow-md" showCloseButton={false}>
          <DrawerHeader className="shrink-0 border-b border-border px-5 py-3">
            <div className="flex items-center justify-between">
              <DrawerTitle className="truncate text-sm font-semibold">{t("scheduler.drawer.title")}</DrawerTitle>
              <DrawerClose
                aria-label={t("ui.drawer.close")}
                className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-4" />
              </DrawerClose>
            </div>
          </DrawerHeader>
          <DrawerBody className="space-y-4">
            {!selectedRun ? (
              <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
                {t("scheduler.drawer.empty")}
              </div>
            ) : selectedRun.status === "running" ? (
              <div className="flex flex-col items-center gap-3 px-4 py-10 text-sm text-muted-foreground">
                <Loader2 className="size-6 animate-spin text-primary" />
                <span>{t("scheduler.drawer.running")}</span>
                {isRepairableRun(selectedRun) ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleRepairRun(selectedRun)}
                    disabled={busyAction === `repair-run:${selectedRun.id}`}
                  >
                    {busyAction === `repair-run:${selectedRun.id}` ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                    {t("scheduler.drawer.repairRunning")}
                  </Button>
                ) : null}
                <div className="w-full space-y-2 pt-2">
                  <div className="h-3 w-3/4 animate-pulse rounded bg-muted" />
                  <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
                  <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
                </div>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{t("scheduler.drawer.runId")}: {selectedRun.run_id}</Badge>
                  <Badge variant="outline">{t("scheduler.drawer.status")}: {selectedRunStatusText}</Badge>
                  <Badge variant="outline">
                    Schedule: {selectedRunTargetMeta.scheduleName} #{selectedRunTargetMeta.scheduleId}
                  </Badge>
                  <Badge variant="outline">
                    {t("scheduler.drawer.target")}: {selectedRunTargetMeta.type} {selectedRunTargetMeta.name}
                    {selectedRunTargetMeta.id ? ` #${selectedRunTargetMeta.id}` : ""}
                  </Badge>
                  {isRepairableRun(selectedRun) ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleRepairRun(selectedRun)}
                      disabled={busyAction === `repair-run:${selectedRun.id}`}
                    >
                      {busyAction === `repair-run:${selectedRun.id}` ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                      {t("scheduler.drawer.repairRunning")}
                    </Button>
                  ) : null}
                </div>
                <CodeBlock label={t("scheduler.drawer.inputPayload")} content={selectedInputPayloadText} maxHeight="220px" />
                {isSelectedAgentTarget ? (
                  <CodeBlock label={t("scheduler.drawer.agentPrompt")} content={selectedAgentPromptText || "-"} maxHeight="180px" />
                ) : null}
                <CodeBlock label={t("scheduler.drawer.output")} content={selectedRunOutputText} maxHeight="360px" />
                <div className="overflow-hidden rounded-lg border border-border bg-card">
                  <div className="border-b border-border bg-muted px-3 py-2">
                    <p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">{t("scheduler.drawer.metaTitle")}</p>
                  </div>
                  <dl className="grid grid-cols-[96px_1fr] text-xs">
                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.triggerType")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">{selectedRun.trigger_type}</dd>

                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.retryRound")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">
                      {selectedRun.attempt}/{Math.max((selectedRun.max_retries ?? 0) + 1, 1)}
                    </dd>

                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.startTime")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">{toDisplayTime(selectedRun.started_at)}</dd>

                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.endTime")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">{toDisplayTime(selectedRun.finished_at)}</dd>

                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.scheduleStatus")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">{selectedRun.status || "-"}</dd>

                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.runtimeStatus")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">{selectedRun.runtime_status || "-"}</dd>

                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.errorSummary")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">{selectedRun.error_summary || "-"}</dd>

                    <dt className="border-t border-border bg-muted px-3 py-2 text-muted-foreground">{t("scheduler.drawer.conversationId")}</dt>
                    <dd className="border-t border-border px-3 py-2 font-medium text-foreground">{selectedRun.conversation_id || "-"}</dd>
                  </dl>
                </div>
              </>
            )}
          </DrawerBody>
        </DrawerContent>
      </Drawer>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="!w-[min(1100px,96vw)] !max-w-[min(1100px,96vw)] h-[88vh] overflow-hidden p-0">
          <div className="flex h-full min-h-0 flex-col">
            <DialogHeader className="border-b border-border px-6 py-4 pr-12">
              <DialogTitle>{t("scheduler.create.title")}</DialogTitle>
              <DialogDescription>{t("scheduler.create.desc")}</DialogDescription>
            </DialogHeader>
            <div className="flex min-h-0 flex-1 overflow-hidden">
              {/* Left column: target selection */}
              <div className="flex w-[280px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-border bg-muted/40 p-4">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t("scheduler.create.selectTarget")}</p>
                  <span className="text-xs text-muted-foreground">
                    {form.target_type === "agent" ? `${t("scheduler.create.availableCount")} ${activeAgents.length}` : `${t("scheduler.create.releasedCount")} ${releasedFunctions.length}`}
                  </span>
                </div>
                <Tabs value={form.target_type === "agent" ? "agent" : "function"} onValueChange={(v) => setForm((prev) => ({ ...prev, target_type: v as ScheduleTargetType, target_id: "" }))}>
                  <TabsList className="w-full">
                    <TabsTrigger value="function" className="flex-1">Function</TabsTrigger>
                    <TabsTrigger value="agent" className="flex-1">Agent</TabsTrigger>
                  </TabsList>
                </Tabs>
                {form.target_type !== "agent" ? (
                  releasedFunctions.length === 0 ? (
                    <div className="rounded-lg border border-dashed bg-card px-3 py-8 text-center text-sm text-muted-foreground">
                      {t("scheduler.create.noFunction")}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {releasedFunctions.map((item) => {
                        const selected = String(item.id) === form.target_id
                        return (
                          <button
                            key={item.id}
                            type="button"
                            onClick={() => setForm((prev) => ({ ...prev, target_type: "function", target_id: String(item.id) }))}
                            className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                              selected ? "border-primary/40 bg-card shadow-sm" : "border-border bg-card hover:bg-muted/60"
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
                              <span className="shrink-0 text-xs text-muted-foreground">#{item.id}</span>
                            </div>
                            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{item.description || t("scheduler.create.noDesc")}</p>
                          </button>
                        )
                      })}
                    </div>
                  )
                ) : (
                  activeAgents.length === 0 ? (
                    <div className="rounded-lg border border-dashed bg-card px-3 py-8 text-center text-sm text-muted-foreground">
                      {t("scheduler.create.noAgent")}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {activeAgents.map((item) => {
                        const selected = String(item.id) === form.target_id
                        return (
                          <button
                            key={item.id}
                            type="button"
                            onClick={() => setForm((prev) => ({ ...prev, target_type: "agent", target_id: String(item.id) }))}
                            className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                              selected ? "border-primary/40 bg-card shadow-sm" : "border-border bg-card hover:bg-muted/60"
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
                              <span className="shrink-0 text-xs text-muted-foreground">#{item.id}</span>
                            </div>
                            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{item.description || t("scheduler.create.noDesc")}</p>
                          </button>
                        )
                      })}
                    </div>
                  )
                )}
              </div>
              {/* Right column: configuration */}
              <div className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
                {/* Name & description */}
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted-foreground">{t("scheduler.create.scheduleName")} <span className="text-destructive">*</span></p>
                    <Input
                      placeholder={t("scheduler.create.namePlaceholder")}
                      value={form.name}
                      onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                    />
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted-foreground">{t("scheduler.create.descLabel")}</p>
                    <Input
                      placeholder={t("scheduler.create.descPlaceholder")}
                      value={form.description}
                      onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                    />
                  </div>
                </div>
                <div className="border-t border-border" />
                {/* Datasource */}
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">{t("scheduler.create.datasourceLabel")}</p>
                  <Select value={form.datasource_id || "__none__"} onValueChange={(v) => setForm((prev) => ({ ...prev, datasource_id: v === "__none__" ? "" : v }))}>
                    <SelectTrigger className="h-9 w-full bg-card"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">{t("scheduler.create.datasourceNone")}</SelectItem>
                      {activeDatasources.map((item) => (
                        <SelectItem key={item.id} value={String(item.id)}>#{item.id} {item.name} · {item.host}:{item.port}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="border-t border-border" />
                {/* AI scheduling intent */}
                <div className="space-y-1">
                  <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                    <Sparkles className="size-3.5" />
                    {t("scheduler.create.intentLabel")}
                  </p>
                  <Textarea
                    placeholder={t("scheduler.create.intentPlaceholder")}
                    value={aiCreatePrompt}
                    onChange={(event) => setAiCreatePrompt(event.target.value)}
                    className="min-h-[72px] bg-card"
                  />
                </div>
                <div className="border-t border-border" />
                {/* Input payload */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-medium text-muted-foreground">{t("scheduler.create.payloadLabel")}</p>
                    {form.target_type !== "agent" && (
                      <Button variant="ghost" size="sm" className="h-6 gap-1 px-2 text-xs" onClick={handleSuggestPayload} disabled={suggestingPayload || !form.target_id}>
                        {suggestingPayload ? <Loader2 className="size-3 animate-spin" /> : <Sparkles className="size-3" />}
                        {t("scheduler.create.aiGenerate")}
                      </Button>
                    )}
                  </div>
                  <Textarea
                    placeholder='{"tenant_id": 1001}'
                    value={form.input_payload_text}
                    onChange={(event) => setForm((prev) => ({ ...prev, input_payload_text: event.target.value }))}
                    className="min-h-[100px] bg-card font-mono text-xs"
                  />
                </div>
                {selectedFunctionContract.length > 0 && form.target_type !== "agent" && (
                  <div className="rounded-lg border border-border bg-muted/40 p-3">
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("scheduler.create.contractTitle")}</p>
                    <div className="space-y-1.5">
                      {selectedFunctionContract.map((field) => (
                        <div key={field.name} className="flex items-baseline gap-2 text-xs">
                          <span className="font-medium text-foreground">{field.name}</span>
                          <span className="text-muted-foreground">({field.type})</span>
                          {field.required ? <span className="text-destructive">{t("scheduler.create.required")}</span> : <span className="text-muted-foreground">{t("scheduler.create.optional")}</span>}
                          {field.description && <span className="text-muted-foreground">— {field.description}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {/* Advanced settings */}
                <details className="rounded-lg border border-border bg-card">
                  <summary className="cursor-pointer px-4 py-2.5 text-xs font-medium text-muted-foreground">{t("scheduler.create.advancedSettings")}</summary>
                  <div className="grid gap-3 border-t border-border px-4 py-3 sm:grid-cols-3">
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">{t("scheduler.create.timezone")}</p>
                      <Select value={form.timezone} onValueChange={(v) => setForm((prev) => ({ ...prev, timezone: v }))}>
                        <SelectTrigger className="h-9 bg-card"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="Asia/Shanghai">{t("scheduler.create.timezoneDefault")}</SelectItem>
                          <SelectItem value="UTC">UTC</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">{t("scheduler.create.maxRetries")}</p>
                      <Input inputMode="numeric" placeholder="0" value={form.max_retries} onChange={(event) => setForm((prev) => ({ ...prev, max_retries: event.target.value }))} className="h-9" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">{t("scheduler.create.retryBackoff")}</p>
                      <Input inputMode="numeric" placeholder="60" value={form.retry_backoff_seconds} onChange={(event) => setForm((prev) => ({ ...prev, retry_backoff_seconds: event.target.value }))} className="h-9" />
                    </div>
                  </div>
                </details>
              </div>
            </div>
            <DialogFooter className="border-t border-border bg-card px-6 py-3">
              <Button variant="outline" onClick={() => setCreateOpen(false)}>{t("scheduler.btn.cancel")}</Button>
              <Button onClick={handleCreateByAi} disabled={busyAction === "ai-create" || !form.target_id || !form.name.trim()}>
                {busyAction === "ai-create" ? <Loader2 className="size-4 animate-spin" /> : <Bot className="size-4" />}
                {t("scheduler.btn.createSubmit")}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="!w-[min(1500px,98vw)] !max-w-[min(1500px,98vw)] h-[90vh] overflow-hidden p-0">
          <div className="flex h-full min-h-0 flex-col">
            <DialogHeader className="border-b border-border px-6 py-4 pr-12">
              <DialogTitle>{editingIsBuiltIn ? t("scheduler.builtInEditTitle") : t("scheduler.edit.title")}</DialogTitle>
              <DialogDescription>
                {editingIsBuiltIn ? t("scheduler.builtInEditDesc") : t("scheduler.edit.desc")}
              </DialogDescription>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-hidden px-6 py-4">
              <div className="grid h-full min-h-0 gap-4 md:grid-cols-[1.05fr_1fr]">
                <div className="min-h-0 space-y-3 overflow-auto rounded-lg border border-border bg-muted p-4">
              {!editingIsBuiltIn ? (
                <>
                  <Input
                    placeholder={t("scheduler.edit.namePlaceholder")}
                    value={form.name}
                    onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                  />
                  <Textarea
                    placeholder={t("scheduler.edit.descPlaceholder")}
                    value={form.description}
                    onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                    className="min-h-20"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <Select value={form.target_type} onValueChange={(v) => setForm((prev) => ({ ...prev, target_type: v as ScheduleTargetType, target_id: "" }))}>
                      <SelectTrigger className="h-10 bg-card"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="function">function</SelectItem>
                        <SelectItem value="agent">agent</SelectItem>
                      </SelectContent>
                    </Select>
                    <Select value={form.target_id || "__none__"} onValueChange={(v) => setForm((prev) => ({ ...prev, target_id: v === "__none__" ? "" : v }))}>
                      <SelectTrigger className="h-10 bg-card"><SelectValue placeholder={targetPlaceholder()} /></SelectTrigger>
                      <SelectContent>
                        {renderTargetItems()}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-foreground">{t("scheduler.edit.datasourceLabel")}</p>
                    <Select value={form.datasource_id || "__none__"} onValueChange={(v) => setForm((prev) => ({ ...prev, datasource_id: v === "__none__" ? "" : v }))}>
                      <SelectTrigger className="h-10 w-full bg-card"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__none__">{t("scheduler.edit.datasourceNone")}</SelectItem>
                        {activeDatasources.map((item) => (
                          <SelectItem key={item.id} value={String(item.id)}>#{item.id} {item.name} · {item.host}:{item.port} · {item.user || "-"}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </>
              ) : (
                <div className="rounded-lg border border-border bg-card p-3 text-xs text-muted-foreground">
                  <p className="font-medium text-foreground">{t("scheduler.builtInReadonlyTitle")}</p>
                  <p className="mt-1">{t("scheduler.builtInReadonlyDesc")}</p>
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <Select value={form.schedule_type} onValueChange={(v) => setForm((prev) => ({ ...prev, schedule_type: v as ScheduleType }))}>
                  <SelectTrigger className="h-10 bg-card"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cron">cron</SelectItem>
                    <SelectItem value="interval">interval</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={form.timezone} onValueChange={(v) => setForm((prev) => ({ ...prev, timezone: v }))}>
                  <SelectTrigger className="h-10 bg-card"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Asia/Shanghai">{t("scheduler.create.timezoneDefault")}</SelectItem>
                    <SelectItem value="UTC">UTC</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.schedule_type === "cron" ? (
                <Input
                  placeholder="cron expression"
                  value={form.cron_expression}
                  onChange={(event) => setForm((prev) => ({ ...prev, cron_expression: event.target.value }))}
                />
              ) : (
                <Input
                  placeholder="interval seconds"
                  value={form.interval_seconds}
                  onChange={(event) => setForm((prev) => ({ ...prev, interval_seconds: event.target.value }))}
                />
              )}
              <div className="space-y-1">
                <p className="text-xs font-medium text-foreground">{t("scheduler.edit.statusLabel")}</p>
                <Select value={form.status} onValueChange={(v) => setForm((prev) => ({ ...prev, status: v as ScheduleStatus }))}>
                  <SelectTrigger className="h-10 bg-card"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">active</SelectItem>
                    <SelectItem value="paused">paused</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {!editingIsBuiltIn ? (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-foreground">{t("scheduler.edit.maxRetries")}</p>
                      <Input
                        inputMode="numeric"
                        placeholder={t("scheduler.edit.retryPlaceholder")}
                        value={form.max_retries}
                        onChange={(event) => setForm((prev) => ({ ...prev, max_retries: event.target.value }))}
                      />
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-foreground">{t("scheduler.edit.retryBackoff")}</p>
                      <Input
                        inputMode="numeric"
                        placeholder={t("scheduler.edit.retryBackoffPlaceholder")}
                        value={form.retry_backoff_seconds}
                        onChange={(event) => setForm((prev) => ({ ...prev, retry_backoff_seconds: event.target.value }))}
                      />
                    </div>
                  </div>
                  <p className="text-[11px] text-muted-foreground">{t("scheduler.edit.retryNote")}</p>
                  <Textarea
                    placeholder="input prompt (agent target)"
                    value={form.input_prompt}
                    onChange={(event) => setForm((prev) => ({ ...prev, input_prompt: event.target.value }))}
                    className="min-h-20"
                  />
                  <Textarea
                    placeholder='input payload JSON, e.g. {"datasource_id":1}'
                    value={form.input_payload_text}
                    onChange={(event) => setForm((prev) => ({ ...prev, input_payload_text: event.target.value }))}
                    className="min-h-24"
                  />
                </>
              ) : null}
                </div>

                <div className="min-h-0 space-y-3 overflow-auto rounded-lg border border-border bg-card p-4">
                  <p className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <Sparkles className="size-4" />
                    {t("scheduler.edit.aiAdjustTitle")}
                  </p>
                  {editingIsBuiltIn ? (
                    <div className="rounded-lg border border-border bg-muted p-3 text-xs text-muted-foreground">
                      {t("scheduler.builtInAiReadonlyNotice")}
                    </div>
                  ) : (
                    <>
                      <Textarea
                        placeholder={t("scheduler.edit.aiAdjustPlaceholder")}
                        value={aiBuildPrompt}
                        onChange={(event) => setAiBuildPrompt(event.target.value)}
                        className="min-h-[58vh] border-border bg-card"
                      />
                      <Button onClick={handleAiBuild} disabled={busyAction === "ai-build"} className="w-full bg-primary text-white hover:bg-primary/90">
                        {busyAction === "ai-build" ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                        {t("scheduler.edit.aiAdjustBtn")}
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </div>
            <DialogFooter className="border-t border-border px-6 py-4">
              <Button variant="outline" onClick={() => setEditOpen(false)}>{t("scheduler.btn.cancel")}</Button>
              <Button onClick={handleSaveEdit} disabled={busyAction === "save-edit"}>
                {busyAction === "save-edit" ? <Loader2 className="size-4 animate-spin" /> : null}
                {t("scheduler.btn.save")}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmActionDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open)
          if (!open) setDeleteTargetId(null)
        }}
        title={t("scheduler.delete.title")}
        description={
          deleteTargetSchedule
            ? `${deleteTargetSchedule.name}(#${deleteTargetSchedule.id}) ${t("scheduler.delete.descWithName")}`
            : t("scheduler.delete.descGeneric")
        }
        confirmText={t("scheduler.delete.confirm")}
        confirming={busyAction === "delete"}
        confirmDisabled={!deleteTargetId}
        onConfirm={handleDelete}
      />
    </>
  )
}
