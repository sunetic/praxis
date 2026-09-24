import type { ShellTranslatorFn } from "@/i18n/shellI18n"
import type { Agent, Schedule, ScheduleRun, ScheduleTargetType as ApiScheduleTargetType } from "@/lib/api"

export type ScheduleTargetType = ApiScheduleTargetType
export type ScheduleType = "cron" | "interval"
export type ScheduleStatus = "active" | "paused"

export type ScheduleFormState = {
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

export type FunctionContractField = {
  name: string
  type: string
  required: boolean
  description: string
}

export type FunctionSummary = {
  id?: number
  name?: string
  status?: string
  description?: string
  draft_dependencies?: unknown
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

export function getErrorMessage(error: unknown, fallback: string): string {
  if (isRecord(error) && typeof error.message === "string" && error.message.trim()) {
    return error.message
  }
  return fallback
}

export const EMPTY_FORM: ScheduleFormState = {
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

export const SCHEDULE_PAGE_SIZE = 10
export const RUN_PAGE_SIZE = 20

export function toDisplayTime(value?: string | null): string {
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

export function scheduleExpression(item: Schedule): string {
  if (item.schedule_type === "interval") {
    return `every ${item.interval_seconds ?? 0}s`
  }
  return item.cron_expression || "-"
}

export function parsePayloadOrThrow(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const parsed = JSON.parse(trimmed)
  if (parsed === null) return null
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("input_payload must be a JSON object")
  }
  return parsed as Record<string, unknown>
}

export function formatJsonLike(value: unknown, fallback = "-"): string {
  if (value === null || value === undefined) return fallback
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value ?? fallback)
  }
}

export function describeRunStatus(run: ScheduleRun, t: ShellTranslatorFn): string {
  if (run.target_type === "agent") {
    const labels: Record<string, Parameters<typeof t>[0]> = {
      queued: "runtime.queued", running: "runtime.executing",
      waiting_approval: "runtime.awaitingApproval", finished: "runtime.finished",
      cancelled: "runtime.stopped", limited: "runtime.runLimitReached",
      failed: "runtime.runFailed", interrupted: "runtime.interruptedReconcileBeforeResuming",
    }
    if (labels[run.status]) return t(labels[run.status])
  }
  const schedulerStatus = String(run.status || "").trim() || "-"
  const runtimeStatus = String(run.runtime_status || "").trim()
  if (!runtimeStatus || runtimeStatus === schedulerStatus) return schedulerStatus
  return `${schedulerStatus} · runtime=${runtimeStatus}`
}

export function isRepairableRun(run: ScheduleRun | null): boolean {
  return run?.target_type !== "agent" && String(run?.status || "").trim().toLowerCase() === "running"
}

export function extractFunctionInputContract(fn: FunctionSummary | null): FunctionContractField[] {
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

export function resolveRunTargetMeta(
  run: ScheduleRun,
  scheduleById: Map<number, Schedule>,
  functionById: Map<number, FunctionSummary>,
  agentById: Map<number, Agent>
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
  return {
    scheduleName: schedule?.name || "-",
    scheduleId,
    type: targetType || "-",
    name: "-",
    id: Number.isInteger(targetId) && targetId > 0 ? targetId : null,
  }
}
