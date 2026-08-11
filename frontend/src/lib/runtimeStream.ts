export type RuntimeCoreEventName = "plan" | "act" | "observe" | "reflect" | "retry" | "done" | "error"

export type RuntimeCoreEvent = {
  kind: "core"
  name: RuntimeCoreEventName
  status: string
  summary: string
  payload: Record<string, unknown>
  source: string
  agent: string
}

export type RuntimeAssistantEvent = {
  kind: "assistant"
  text: string
  source: string
  agent: string
}

export type RuntimeExtensionEvent = {
  kind: "extension"
  name: string
  summary: string
  payload: Record<string, unknown>
  source: string
  agent: string
}

export type RuntimeSkillDeltaEvent = {
  kind: "skill_delta"
  active_skills: string[]
  added: string[]
  removed: string[]
  reason: string
}

export type RuntimeNormalizedEvent = RuntimeCoreEvent | RuntimeAssistantEvent | RuntimeExtensionEvent | RuntimeSkillDeltaEvent

type ConsumeRuntimeSseOptions = {
  requireDone?: boolean
  onEvent?: (event: RuntimeNormalizedEvent, raw: Record<string, unknown>) => void
}

type ConsumeRuntimeSseResult = {
  donePayload: Record<string, unknown>
  assistantText: string
  /** Set when the stream ended with a backend error event (not a JS exception). */
  terminalError?: string
}

const CORE_PHASE_SET = new Set<RuntimeCoreEventName>(["plan", "act", "observe", "reflect", "retry", "done", "error"])

const CORE_PHASE_FALLBACK_MAP: Record<string, RuntimeCoreEventName> = {
  intake: "plan",
  intent_parsed: "plan",
  draft_planned: "plan",
  plan_generated: "plan",
  reuse_recommendation: "plan",
  code_generated: "act",
  patch_applied: "act",
  apply: "act",
  suggest_input: "act",
  invoke_started: "act",
  patch_validated: "observe",
  preview_ready: "observe",
  verify_failed: "observe",
  invoke_finished: "observe",
  failed: "observe",
}

const CORE_STAGE_LABEL_MAP: Record<Exclude<RuntimeCoreEventName, "done" | "error">, string> = {
  plan: "需求规划",
  act: "变更执行",
  observe: "结果校验",
  reflect: "反思调整",
  retry: "重试执行",
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {}
}

function toCoreName(value: string): RuntimeCoreEventName | null {
  const normalized = String(value || "").trim().toLowerCase()
  if (CORE_PHASE_SET.has(normalized as RuntimeCoreEventName)) {
    return normalized as RuntimeCoreEventName
  }
  if (normalized in CORE_PHASE_FALLBACK_MAP) {
    return CORE_PHASE_FALLBACK_MAP[normalized]
  }
  return null
}

function toSummaryText(data: Record<string, unknown>, fallback = ""): string {
  const summary = String(data.summary || data.message || fallback || "").trim()
  return summary
}

function stripCorePrefix(summary: string): string {
  return String(summary || "")
    .replace(/^(plan|act|observe|reflect|retry)\s*[·:：-]\s*/i, "")
    .trim()
}

function normalizeCoreSummary(summary: string): string {
  return String(summary || "")
    .replace(/^Attempt\s+(\d+)\s+(?:执行中|executing)$/i, "第$1轮执行中")
    .replace(/^Attempt\s+(\d+)\s+(?:校验中|verifying)$/i, "第$1轮校验中")
    .replace(/^Attempt\s+(\d+)\s+(?:校验通过|passed)$/i, "第$1轮校验通过")
    .replace(/^Attempt\s+(\d+)\s+(?:校验失败|failed)[:：]?\s*/i, "第$1轮校验失败：")
    .replace(/^Attempt\s+(\d+)\s+(?:构建失败|build failed)[:：]?\s*/i, "第$1轮构建失败：")
    .replace(/^(?:发起\s+Attempt|Starting attempt)\s+(\d+)$/i, "开始第$1轮")
    .trim()
}

function normalizeAgentLabel(value: unknown): string {
  const normalized = String(value || "").trim()
  if (!normalized || normalized === "Assistant" || normalized === "Runtime") return ""
  return normalized
}

export function normalizeRuntimeStreamEvent(raw: unknown): RuntimeNormalizedEvent | null {
  const payload = asRecord(raw)
  const type = String(payload.type || "").trim().toLowerCase()
  const data = asRecord(payload.data)

  if (type === "assistant") {
    const text = String(data.text || "")
    return text
      ? {
          kind: "assistant",
          text,
          source: String(data.source || "llm"),
          agent: normalizeAgentLabel(data.agent_name || data.agent),
        }
      : null
  }

  if (type === "error") {
    const summary = String(data.user_message || data.message || "Request failed").trim()
    return {
      kind: "core",
      name: "error",
      status: "failed",
      summary,
      payload: data,
      source: String(data.source || "runtime"),
      agent: normalizeAgentLabel(data.agent_name || data.agent),
    }
  }

  if (type === "done") {
    const doneData = Object.keys(data).length > 0 ? data : payload
    const summary = toSummaryText(doneData, "Completed")
    return {
      kind: "core",
      name: "done",
      status: String(doneData.status || "done"),
      summary,
      payload: doneData,
      source: String(doneData.source || "runtime"),
      agent: normalizeAgentLabel(doneData.agent_name || doneData.agent),
    }
  }

  if (type === "skill_delta") {
    // data channel items have their fields spread to the top level (no nested .data),
    // so read from payload directly, falling back to data for SSE phase-channel format.
    const src = Array.isArray(payload.active_skills) ? payload : data
    return {
      kind: "skill_delta",
      active_skills: Array.isArray(src.active_skills) ? (src.active_skills as string[]) : [],
      added: Array.isArray(src.added) ? (src.added as string[]) : [],
      removed: Array.isArray(src.removed) ? (src.removed as string[]) : [],
      reason: String(src.reason || ""),
    }
  }

  if (type === "save_agent_status" || type === "save_agent_done") {
    return {
      kind: "extension",
      name: type,
      summary: toSummaryText(data, "Extension event"),
      payload: data,
      source: String(data.source || "runtime"),
      agent: normalizeAgentLabel(data.agent_name || data.agent),
    }
  }

  if (
    type === "task_contract" ||
    type === "assistant_progress" ||
    type === "progress" ||
    type === "verification" ||
    type === "task_state" ||
    type === "checkpoint" ||
    type === "context_compressed"
  ) {
    const eventPayload = Object.keys(data).length > 0 ? data : payload
    return {
      kind: "extension",
      name: type,
      summary: type === "assistant_progress"
        ? String(eventPayload.text || eventPayload.message || "").trim()
        : toSummaryText(eventPayload, ""),
      payload: eventPayload,
      source: String(eventPayload.source || "runtime"),
      agent: normalizeAgentLabel(eventPayload.agent_name || eventPayload.agent),
    }
  }

  if (type === "phase") {
    const eventGroup = String(payload.event_group || "").trim().toLowerCase()
    const eventName = String(payload.event_name || "").trim()
    if (eventGroup === "extension" || (eventName && toCoreName(eventName) === null)) {
      return {
        kind: "extension",
        name: String(eventName || payload.phase || "extension"),
        summary: toSummaryText(data, "Extension event"),
        payload: data,
        source: String(data.source || "runtime"),
        agent: normalizeAgentLabel(data.agent_name || data.agent),
      }
    }
    const phaseName = toCoreName(String(eventName || payload.phase || data.phase || ""))
    if (!phaseName) {
      return {
        kind: "extension",
        name: String(eventName || payload.phase || "extension"),
        summary: toSummaryText(data, "Extension event"),
        payload: data,
        source: String(data.source || "runtime"),
        agent: normalizeAgentLabel(data.agent_name || data.agent),
      }
    }
    const phasePayload = asRecord(data.payload)
    return {
      kind: "core",
      name: phaseName,
      status: String(data.status || payload.status || "running"),
      summary: toSummaryText(data),
      payload: phasePayload,
      source: String(phasePayload.source || data.source || "runtime"),
      agent: normalizeAgentLabel(phasePayload.agent_name || phasePayload.agent || data.agent_name || data.agent),
    }
  }

  if (type === "thinking" || type === "plan" || type === "reflect" || type === "retry") {
    const coreName = toCoreName(type)
    if (!coreName) return null
    return {
      kind: "core",
      name: coreName,
      status: String(data.status || "running"),
      summary: toSummaryText(data),
      payload: data,
      source: String(data.source || "runtime"),
      agent: normalizeAgentLabel(data.agent_name || data.agent),
    }
  }

  if (type === "extension" || type === "tool_start" || type === "tool_result" || type === "step_start" || type === "step_result" || type === "verify_result" || type === "preview_result") {
    const extensionName = type === "extension"
      ? String(payload.event_name || data.name || "extension")
      : (type === "step_start" ? "tool_start" : type === "step_result" ? "tool_result" : type)
    return {
      kind: "extension",
      name: extensionName,
      summary: toSummaryText(data, "Extension event"),
      payload: data,
      source: String(data.source || "runtime"),
      agent: normalizeAgentLabel(data.agent_name || data.agent),
    }
  }

  return null
}

export function formatRuntimeCoreMessage(
  event: RuntimeCoreEvent,
  options?: { includeAgent?: boolean }
): string {
  if (event.name === "done") {
    return event.summary || "Completed"
  }
  if (event.name === "error") {
    return event.summary || "Failed"
  }
  const summary = normalizeCoreSummary(stripCorePrefix(String(event.summary || "")))
  const stageLabel = CORE_STAGE_LABEL_MAP[event.name]
  const hasStagePrefix = summary.startsWith(`${stageLabel} ·`) || summary.startsWith(`${stageLabel}:`)
  const baseText = summary
    ? (hasStagePrefix ? summary : `${stageLabel} · ${summary}`)
    : `${stageLabel} · Processing`
  const agentLabel = normalizeAgentLabel(event.agent)
  const includeAgent = options?.includeAgent !== false
  const base = includeAgent && agentLabel ? `${agentLabel} · ${baseText}` : baseText
  const phaseDuration = Number(event.payload.phase_duration_ms || 0)
  if (Number.isFinite(phaseDuration) && phaseDuration > 0) {
    return `${base}（${Math.trunc(phaseDuration)}ms）`
  }
  return base
}

// ── Vercel Data Stream (VDS) parser ───────────────────────────────────
// Line format: `<code>:<json>\n`
// Codes: 0=text, g=reasoning, 9=tool_call, a=tool_result, 2=data[], e=finish_step, d=finish_message

function parseVdsLine(line: string): RuntimeNormalizedEvent | null {
  const colonIdx = line.indexOf(":")
  if (colonIdx < 0) return null
  const code = line.slice(0, colonIdx)
  const jsonStr = line.slice(colonIdx + 1)
  let value: unknown
  try {
    value = JSON.parse(jsonStr)
  } catch {
    return null
  }

  if (code === "0") {
    const text = typeof value === "string" ? value : ""
    if (!text) return null
    return { kind: "assistant", text, source: "llm", agent: "" }
  }

  if (code === "g") {
    const text = typeof value === "string" ? value : ""
    if (!text) return null
    return { kind: "core", name: "plan", status: "running", summary: text, payload: { thinking: text }, source: "llm", agent: "" }
  }

  if (code === "9") {
    const obj = asRecord(value)
    return {
      kind: "extension",
      name: "tool_start",
      summary: String(obj.toolName || ""),
      payload: {
        step_id: String(obj.toolCallId || ""),
        name: String(obj.toolName || ""),
        arguments: typeof obj.args === "object" ? JSON.stringify(obj.args) : String(obj.args ?? ""),
        kind: "tool",
      },
      source: "runtime",
      agent: "",
    }
  }

  if (code === "a") {
    const obj = asRecord(value)
    return {
      kind: "extension",
      name: "tool_result",
      summary: "",
      payload: {
        step_id: String(obj.toolCallId || ""),
        result: obj.result,
        kind: "tool",
      },
      source: "runtime",
      agent: "",
    }
  }

  if (code === "d") {
    const obj = asRecord(value)
    const reason = String(obj.finishReason || "stop")
    if (reason === "error") {
      return { kind: "core", name: "error", status: "failed", summary: "Request failed", payload: obj, source: "runtime", agent: "" }
    }
    return { kind: "core", name: "done", status: "done", summary: "Completed", payload: obj, source: "runtime", agent: "" }
  }

  // e = finish_step, 2 = data channel (handled separately in consumeVds)
  return null
}

export async function consumeVds(
  response: Response,
  options: ConsumeRuntimeSseOptions = {}
): Promise<ConsumeRuntimeSseResult> {
  if (!response.ok || !response.body) {
    throw new Error("No usable stream returned. Please retry.")
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder("utf-8")
  let buffer = ""
  let donePayload: Record<string, unknown> | null = null
  let assistantText = ""
  let terminalError: string | undefined = undefined

  outer: while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""

    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (!line) continue

      const colonIdx = line.indexOf(":")
      if (colonIdx >= 0 && line.slice(0, colonIdx) === "2") {
        let items: unknown[]
        try {
          const parsed = JSON.parse(line.slice(colonIdx + 1))
          items = Array.isArray(parsed) ? parsed : []
        } catch {
          continue
        }
        let shouldBreak = false
        for (const item of items) {
          if (!item || typeof item !== "object") continue
          const obj = asRecord(item)
          const type = String(obj.type || "")
          if (!type) continue
          const event = normalizeRuntimeStreamEvent(item)
          if (!event) continue
          options.onEvent?.(event, obj)
          if (event.kind === "core" && event.name === "error") {
            terminalError = event.summary || "Request failed"
            donePayload = donePayload ?? {}
            shouldBreak = true
          }
          if (event.kind === "core" && event.name === "done") {
            donePayload = asRecord(event.payload)
            shouldBreak = true
          }
        }
        await new Promise((r) => setTimeout(r, 0))
        if (shouldBreak) break outer
        continue
      }

      const event = parseVdsLine(line)
      if (!event) continue
      options.onEvent?.(event, {})

      if (event.kind === "assistant") {
        assistantText += event.text
      } else if (event.kind === "core" && event.name === "error") {
        terminalError = event.summary || "Request failed"
        donePayload = donePayload ?? {}
        break outer
      } else if (event.kind === "core" && event.name === "done") {
        donePayload = asRecord(event.payload)
        break outer
      }

      await new Promise((r) => setTimeout(r, 0))
    }
  }

  if (options.requireDone !== false && donePayload === null) {
    throw new Error("Response stream ended unexpectedly. Please retry.")
  }
  return {
    donePayload: donePayload || {},
    assistantText,
    terminalError,
  }
}

export async function consumeRuntimeSse(
  response: Response,
  options: ConsumeRuntimeSseOptions = {}
): Promise<ConsumeRuntimeSseResult> {
  if (!response.ok || !response.body) {
    throw new Error("No usable stream returned. Please retry.")
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder("utf-8")
  let buffer = ""
  let donePayload: Record<string, unknown> | null = null
  let assistantText = ""
  let terminalError: string | undefined = undefined

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split("\n\n")
    buffer = chunks.pop() || ""
    for (const chunk of chunks) {
      const line = chunk
        .split("\n")
        .map((part) => part.trim())
        .find((part) => part.startsWith("data:"))
      if (!line) continue
      const rawText = line.replace(/^data:\s*/, "")
      if (!rawText || rawText === "[DONE]") continue

      let parsed: Record<string, unknown>
      try {
        parsed = JSON.parse(rawText) as Record<string, unknown>
      } catch {
        continue
      }
      const event = normalizeRuntimeStreamEvent(parsed)
      if (!event) continue
      options.onEvent?.(event, parsed)

      if (event.kind === "assistant") {
        assistantText += event.text
        continue
      }
      if (event.kind === "core" && event.name === "error") {
        // Treat backend error events as a terminal state rather than a JS exception.
        // The caller can inspect terminalError and decide how to present it.
        terminalError = event.summary || "Request failed"
        donePayload = donePayload ?? {}
        break
      }
      if (event.kind === "core" && event.name === "done") {
        donePayload = asRecord(event.payload)
      }

      // Yield to the event loop so React can flush each state update
      // instead of batching all events from one TCP chunk into a single render.
      await new Promise((r) => setTimeout(r, 0))
    }
  }

  if (options.requireDone !== false && donePayload === null) {
    throw new Error("Response stream ended unexpectedly. Please retry.")
  }
  return {
    donePayload: donePayload || {},
    assistantText,
    terminalError,
  }
}
