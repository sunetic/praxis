import { api } from "./api"

export type RunStatus = "queued" | "running" | "waiting_approval" | "finished" | "cancelled" | "failed" | "limited" | "interrupted"
export type ConversationAutoApproval = { tool_name: "request_database_change"; agent_id: number | null; datasource_id: number; expires_at: number }
export type RunScene = { agent_id?: number; datasource_ids?: number[] | null; knowledge_base_ids?: number[] | null; service_ids?: number[] | null; function_ids?: number[]; page_ids?: number[]; skill_draft_ids?: string[]; skills?: string[]; auto_approval?: ConversationAutoApproval | null }
export type RunConversation = { id: string; title: string; scene: RunScene; created_at: number; active_run_id: string | null }
export type Reconciliation = { resolution: "succeeded" | "failed" | "not_executed"; evidence: string; execution_stopped: boolean }
export type AgentRun = {
  id: string; conversation_id: string; prompt: string; seq: number; status: RunStatus
  cancel_requested: boolean; output: string | null; error_code: string | null; event_seq: number; created_at: number
  model?: { context_window_tokens: number; context_compression_threshold_percent: number } | null
}
export type RunContextStatus = {
  context_window_tokens: number; estimated_tokens: number; used_percent: number
  compression_threshold_percent: number; compression_threshold_tokens: number
  remaining_tokens: number; token_source: "estimate" | "provider"
  state: "ready" | "compressing" | "compression_failed"
}
export type ContextCompressionNotice = {
  before_percent: number; after_percent: number; compacted_message_count: number
}
export type RunEvent = {
  type: string; run_id: string; seq: number; created_at?: number; message_id?: string; part_id?: string; text?: string
  call_id?: string; name?: string; arguments?: Record<string, unknown>; target?: Record<string, unknown>
  fingerprint?: string; decision?: string; status?: string; outcome?: string; content?: unknown; error_code?: string
  automatic?: boolean
  context_window_tokens?: number; estimated_tokens?: number; used_percent?: number
  compression_threshold_percent?: number; compression_threshold_tokens?: number; remaining_tokens?: number
  token_source?: "estimate" | "provider"; state?: RunContextStatus["state"]
  before_context_tokens?: number; after_context_tokens?: number; source_indices?: number[]
}
export const terminalRun = (status: RunStatus) => ["finished", "cancelled", "failed", "limited", "interrupted"].includes(status)

export const agentRunsApi = {
  conversations: async (): Promise<RunConversation[]> => (await api.get("/conversations")).data,
  createConversation: async (title: string, scene: RunScene = {}): Promise<RunConversation> => (await api.post("/conversations", { title, scene })).data,
  updateConversation: async (id: string, scene: RunScene): Promise<RunConversation> => (await api.patch(`/conversations/${encodeURIComponent(id)}`, { scene })).data,
  runs: async (id: string): Promise<AgentRun[]> => (await api.get(`/conversations/${encodeURIComponent(id)}/runs`)).data,
  get: async (id: string): Promise<AgentRun> => (await api.get(`/runs/${encodeURIComponent(id)}`)).data,
  submit: async (id: string, client_request_id: string, prompt: string, scene: RunScene, mode: "append" | "stop_and_modify"): Promise<AgentRun> =>
    (await api.post(`/conversations/${encodeURIComponent(id)}/runs`, { client_request_id, prompt, scene, mode })).data,
  cancel: async (id: string): Promise<AgentRun> => (await api.post(`/runs/${encodeURIComponent(id)}/cancel`)).data,
  decide: async (id: string, callId: string, fingerprint: string, approved: boolean) =>
    (await api.post(`/runs/${encodeURIComponent(id)}/tool-calls/${encodeURIComponent(callId)}/approval`, { fingerprint, approved })).data,
  reconcile: async (id: string, callId: string, fingerprint: string, check: Reconciliation) =>
    (await api.post(`/runs/${encodeURIComponent(id)}/tool-calls/${encodeURIComponent(callId)}/reconciliation`, { fingerprint, ...check })).data,
  resume: async (id: string, expected_event_seq: number): Promise<AgentRun> =>
    (await api.post(`/runs/${encodeURIComponent(id)}/resume`, { expected_event_seq })).data,
  stream: (id: string, after: number, signal: AbortSignal) => fetch(api.getUri({ url: `/runs/${encodeURIComponent(id)}/events` }), {
    signal, headers: { "Last-Event-ID": String(after), Accept: "text/event-stream" }, credentials: "same-origin",
  }),
}

/** Native SSE only. Comments/heartbeats never become assistant content. */
export class RunSubscriptionError extends Error {
  status: number
  constructor(status: number) { super(`Event subscription failed (${status})`); this.status = status }
}

export async function consumeRunEvents(response: Response, accept: (event: RunEvent) => void) {
  if (!response.ok || !response.body) throw new RunSubscriptionError(response.status)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffered = ""
  let data: string[] = []
  const line = (value: string) => {
    if (!value) {
      if (data.length) {
        const event = JSON.parse(data.join("\n")) as RunEvent
        if (typeof event.run_id !== "string" || !Number.isSafeInteger(event.seq) || typeof event.type !== "string") throw new Error("Invalid run event")
        accept(event)
      }
      data = []
    } else if (value.startsWith("data:")) data.push(value.slice(5).replace(/^ /, ""))
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffered += decoder.decode(value, { stream: !done })
      let newline: number
      while ((newline = buffered.indexOf("\n")) >= 0) {
        line(buffered.slice(0, newline).replace(/\r$/, ""))
        buffered = buffered.slice(newline + 1)
      }
      if (done) break
    }
    // An incomplete frame is replayed from the last accepted cursor after reconnect.
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

export type TextBlock = { kind: "text"; id: string; messageId: string; text: string; ended: boolean }
export type ToolBlock = {
  kind: "tool"; id: string; name: string; arguments: Record<string, unknown>; target: Record<string, unknown>
  status: string; fingerprint?: string; decision?: string; result?: unknown; submitting?: boolean; error?: string; reconciled?: boolean; autoApproved?: boolean
}
export type RunView = {
  run: AgentRun; cursor: number; blocks: (TextBlock | ToolBlock)[]
  connection: "connecting" | "connected" | "reconnecting" | "closed" | "unavailable"
  activity: "queued" | "context" | "model" | "text" | "tool" | "approval" | null; activityAt: number; terminalSeen: boolean
  contextStatus?: RunContextStatus; contextCompressionNotice?: ContextCompressionNotice
  delivery?: "sending" | "failed"; clientRequestId?: string; submittedScene?: RunScene; submittedMode?: "append" | "stop_and_modify"
  error?: string
  resuming?: boolean
}
export const runView = (run: AgentRun): RunView => ({ run, cursor: 0, blocks: [], connection: "connecting", activity: "queued", activityAt: run.created_at * 1000, terminalSeen: false })

function contextStatus(event: RunEvent, fallback?: RunContextStatus): RunContextStatus | undefined {
  const window = event.context_window_tokens ?? fallback?.context_window_tokens
  const estimated = event.estimated_tokens ?? event.after_context_tokens ?? event.before_context_tokens ?? fallback?.estimated_tokens
  if (!window || estimated === undefined) return fallback
  return {
    context_window_tokens: window,
    estimated_tokens: estimated,
    used_percent: event.used_percent ?? estimated * 100 / window,
    compression_threshold_percent: event.compression_threshold_percent ?? fallback?.compression_threshold_percent ?? 75,
    compression_threshold_tokens: event.compression_threshold_tokens ?? fallback?.compression_threshold_tokens ?? Math.round(window * (event.compression_threshold_percent ?? fallback?.compression_threshold_percent ?? 75) / 100),
    remaining_tokens: event.remaining_tokens ?? Math.max(0, window - estimated),
    token_source: event.token_source ?? fallback?.token_source ?? "estimate",
    state: event.state ?? fallback?.state ?? "ready",
  }
}

/** An event projection, not a second agent state machine or a text classifier. */
export function applyRunEvent(view: RunView, event: RunEvent): RunView {
  if (event.run_id !== view.run.id || event.seq <= view.cursor) return view
  if (event.seq !== view.cursor + 1) throw new Error("Run event gap; reconnect from the last cursor")
  const next: RunView = { ...view, run: { ...view.run, event_seq: Math.max(view.run.event_seq, event.seq) }, blocks: [...view.blocks], cursor: event.seq, connection: "connected" }
  const activity = (value: RunView["activity"]) => { next.activity = value; next.activityAt = event.created_at === undefined ? Date.now() : event.created_at * 1000 }
  if (event.type === "context_status") next.contextStatus = contextStatus(event, view.contextStatus)
  else if (event.type === "assistant_delta" && event.message_id && event.part_id !== undefined && typeof event.text === "string") {
    if (view.activity !== "text") activity("text")
    const id = `${event.message_id}:${event.part_id}`
    const index = next.blocks.findIndex(block => block.kind === "text" && block.id === id)
    const prior = index >= 0 ? next.blocks[index] as TextBlock : null
    const block: TextBlock = { kind: "text", id, messageId: event.message_id, text: (prior?.text ?? "") + event.text, ended: false }
    if (index >= 0) next.blocks[index] = block
    else next.blocks.push(block)
  } else if (event.type === "assistant_message_end") {
    next.blocks = next.blocks.map(block => block.kind === "text" && block.messageId === event.message_id ? { ...block, ended: true } : block)
  } else if (event.type === "assistant_message_discarded" && event.message_id) {
    next.blocks = next.blocks.filter(block => block.kind !== "text" || block.messageId !== event.message_id)
  } else if (["tool_start", "tool_result", "tool_reconciled", "approval_required", "approval_decided"].includes(event.type) && event.call_id) {
    const index = next.blocks.findIndex(block => block.kind === "tool" && block.id === event.call_id)
    const prior = index >= 0 ? next.blocks[index] as ToolBlock : null
    const tool: ToolBlock = { kind: "tool", id: event.call_id, name: event.name ?? prior?.name ?? event.call_id, arguments: event.arguments ?? prior?.arguments ?? {}, target: event.target ?? prior?.target ?? {}, status: prior?.status ?? "pending", ...prior }
    if (event.name) tool.name = event.name
    if (event.arguments) tool.arguments = event.arguments
    if (event.target) tool.target = event.target
    if (event.fingerprint) tool.fingerprint = event.fingerprint
    if (event.type === "tool_start") { tool.status = "executing"; activity("tool") }
    if (event.type === "tool_result" || event.type === "tool_reconciled") {
      tool.status = event.status ?? (event.outcome === "success" ? "succeeded" : event.outcome === "interrupted" ? "outcome_unknown" : event.outcome ?? "failed")
      tool.result = event.content
      if (event.type === "tool_reconciled") { tool.reconciled = true; tool.submitting = false; tool.error = undefined }
    }
    if (event.type === "approval_required") { tool.status = "waiting_approval"; tool.fingerprint = event.fingerprint; tool.decision = "pending"; activity("approval") }
    if (event.type === "approval_decided") { tool.decision = event.decision; tool.autoApproved = event.automatic === true || prior?.autoApproved }
    if (index >= 0) next.blocks[index] = tool
    else next.blocks.push(tool)
  } else if (event.type === "context_compaction_started") {
    next.run.status = "running"; activity("context")
    next.contextStatus = contextStatus({ ...event, state: "compressing" }, view.contextStatus)
  }
  else if (event.type === "context_compacted") {
    activity("queued")
    next.contextStatus = contextStatus({ ...event, state: "ready" }, view.contextStatus)
    const window = next.contextStatus?.context_window_tokens
    if (window && event.before_context_tokens !== undefined && event.after_context_tokens !== undefined) {
      next.contextCompressionNotice = {
        before_percent: event.before_context_tokens * 100 / window,
        after_percent: event.after_context_tokens * 100 / window,
        compacted_message_count: event.source_indices?.length ?? 0,
      }
    }
  }
  else if (event.type === "request_started") { next.run.status = "running"; activity("model") }
  else if (event.type === "run_started" || event.type === "run_resumed") { next.run.status = event.type === "run_resumed" ? "queued" : "running"; next.terminalSeen = false; next.run.error_code = null; next.resuming = false; activity("queued") }
  else if (event.type === "run_paused") { next.run.status = "waiting_approval"; activity("approval") }
  else if (event.type.startsWith("run_") && event.status && terminalRun(event.status as RunStatus)) {
    next.run.status = event.status as RunStatus
    next.run.error_code = event.error_code ?? null
    next.run.cancel_requested = false
    next.terminalSeen = true
    if (view.contextStatus?.state === "compressing" && event.status !== "finished") next.contextStatus = { ...view.contextStatus, state: "compression_failed" }
    if (event.status === "interrupted") next.blocks = next.blocks.map(block => block.kind === "tool" && block.status === "executing" ? { ...block, status: "outcome_unknown" } : block)
    activity(null)
  }
  return next
}
