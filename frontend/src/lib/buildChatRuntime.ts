export type BuildChatRole = "user" | "assistant" | "status"

export type BuildChatMessageLike = {
  role: BuildChatRole
  content: string
}

export type BuildRunEventLike = {
  phase?: string
  status?: string
  summary?: string
  payload?: Record<string, unknown> | null
}

export type TimelineMessage = {
  id: string
  role: "status"
  content: string
}

export type TimelineEntry = {
  message: TimelineMessage
  phaseMs: number
}

type BuildPhaseTimelineOptions = {
  events: BuildRunEventLike[]
  idPrefix: string
  allowedPhases?: Set<string>
  phaseLabelMap: Record<string, string>
  assistantText?: string
}

type AttemptTimelineOptions = {
  events: BuildRunEventLike[]
  idPrefix: string
}

export type BuildWaitingStage = {
  atMs: number
  label: string
}

function normalizeMessageContent(value: string): string {
  return String(value || "").trim().replace(/\s+/g, " ")
}

function toNumber(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return 0
}

export function formatBuildWaitingText(
  elapsedMs: number,
  stages: BuildWaitingStage[],
  fallback = "Processing"
): string {
  const safeElapsed = Math.max(0, Math.trunc(toNumber(elapsedMs)))
  const normalizedStages = [...stages]
    .filter((stage) => stage && typeof stage.label === "string" && stage.label.trim().length > 0)
    .map((stage) => ({
      atMs: Math.max(0, Math.trunc(toNumber(stage.atMs))),
      label: stage.label.trim(),
    }))
    .sort((a, b) => a.atMs - b.atMs)
  const active = normalizedStages.reduce<BuildWaitingStage | null>(
    (current, stage) => (safeElapsed >= stage.atMs ? stage : current),
    null
  )
  const label = active?.label || fallback
  const elapsedSeconds = Math.floor(safeElapsed / 1000)
  if (elapsedSeconds <= 0) return label
  return `${label} · ${elapsedSeconds}s`
}

export function buildConversationContext(
  messages: BuildChatMessageLike[],
  prompt: string,
  limit = 10
): string {
  return [...messages, { role: "user", content: prompt }]
    .filter((message) => message.role !== "status")
    .slice(-Math.max(1, limit))
    .map((message) => `${message.role}: ${String(message.content || "")}`)
    .join("\n")
}

export function buildPhaseTimelineEntries({
  events,
  idPrefix,
  allowedPhases,
  phaseLabelMap,
  assistantText,
}: BuildPhaseTimelineOptions): TimelineEntry[] {
  const assistantNormalized = normalizeMessageContent(String(assistantText || ""))
  const seenStatusContent = new Set<string>()

  return events
    .filter((event) => {
      if (!allowedPhases || allowedPhases.size === 0) return true
      return allowedPhases.has(String(event?.phase || ""))
    })
    .map((event, idx) => {
      const phase = String(event?.phase || "")
      const fallback = String(phaseLabelMap[phase] || "Processing")
      const summary = String(event?.summary || "").trim()
      const phaseMs = toNumber(event?.payload?.phase_duration_ms)
      const text = summary || fallback
      const content = phaseMs > 0 ? `${text}（${phaseMs}ms）` : text
      return {
        message: {
          id: `${idPrefix}-${idx}`,
          role: "status" as const,
          content,
        },
        phaseMs,
      }
    })
    .filter((entry) => {
      const normalized = normalizeMessageContent(entry.message.content)
      if (!normalized) return false
      if (assistantNormalized && normalized === assistantNormalized) return false
      if (seenStatusContent.has(normalized)) return false
      seenStatusContent.add(normalized)
      return true
    })
}

export function buildAttemptTimelineEntries({
  events,
  idPrefix,
}: AttemptTimelineOptions): TimelineEntry[] {
  const source = [...events]
    .reverse()
    .find((event) => event?.payload && typeof event.payload === "object" && Array.isArray(event.payload.attempts))
  if (!source || !source.payload || typeof source.payload !== "object") return []
  const payload = source.payload as Record<string, unknown>
  const attempts = payload.attempts
  if (!Array.isArray(attempts) || attempts.length <= 1) return []

  const entries: TimelineEntry[] = []
  attempts.forEach((item, idx) => {
    if (!item || typeof item !== "object") return
    const attempt = item as Record<string, unknown>
    const no = Math.max(1, Math.trunc(toNumber(attempt.attempt) || idx + 1))
    const status = String(attempt.status || "").trim().toLowerCase()
    const summary = String(attempt.summary || "").trim()
    const diagnostics = Array.isArray(attempt.diagnostics)
      ? attempt.diagnostics.filter((d): d is string => typeof d === "string" && d.trim().length > 0)
      : []
    if (status === "failed") {
      entries.push({
        message: {
          id: `${idPrefix}-observe-${idx}`,
          role: "status",
          content: `Observe · Attempt ${no} failed${summary ? `: ${summary}` : ""}`,
        },
        phaseMs: 0,
      })
      if (diagnostics[0]) {
        entries.push({
          message: {
            id: `${idPrefix}-reflect-${idx}`,
            role: "status",
            content: `Reflect · ${diagnostics[0]}`,
          },
          phaseMs: 0,
        })
      }
      if (idx < attempts.length - 1) {
        entries.push({
          message: {
            id: `${idPrefix}-retry-${idx}`,
            role: "status",
            content: `Retry · Starting attempt ${no + 1}`,
          },
          phaseMs: 0,
        })
      }
      return
    }
    entries.push({
      message: {
        id: `${idPrefix}-act-${idx}`,
        role: "status",
        content: `Act · Attempt ${no} succeeded${summary ? `: ${summary}` : ""}`,
      },
      phaseMs: 0,
    })
  })

  const unique = new Set<string>()
  return entries.filter((entry) => {
    const key = normalizeMessageContent(entry.message.content)
    if (!key || unique.has(key)) return false
    unique.add(key)
    return true
  })
}
