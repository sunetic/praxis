/**
 * Unified chat controller hook.
 *
 * Single source of truth for chat state management — used by both ChatPage
 * (full mode) and Drawer Chat (embedded mode). Replaces the separate
 * useConversationController and ChatPage inline logic.
 */
import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"
import { useShellI18n } from "@/i18n/shellI18n"

import {
  chatApi,
  conversationsApi,
  messagesApi,
  type ChatEvent,
  type ChatContextStatus,
  type ChatHandoff,
  type ContentPart,
  type ContextCompressionNotice,
  type Conversation,
  type DataSource,
  type Message,
  type PendingAction,
  type SaveAgentStreamEvent,
  type SceneAgentPayload,
} from "@/lib/api"
import {
  consumeVds,
  formatRuntimeCoreMessage,
  type RuntimeCoreEvent,
  type RuntimeExtensionEvent,
  type RuntimeNormalizedEvent,
} from "@/lib/runtimeStream"

// ── Types ──────────────────────────────────────────────────────────────

export type RuntimeStatus = {
  phase: "thinking" | "plan" | "tool" | "reflect"
  text: string
}

export type SaveAgentState = {
  stage: "summarizing" | "saving" | "done" | "error"
  text: string
  agentId?: number
  agentName?: string
  agentUrl?: string
}

export type TenantBadge = {
  text: string
  isSys: boolean
}


type BuilderScopeConfig = {
  scopeObjectType: "page" | "function" | "scheduler"
  scopeObjectId: string
  ttlSeconds?: number
}

export type ChatControllerParams = {
  /** Display title for conversation creation. */
  title: string
  /** Bound datasource, null = none. */
  datasourceId: number | null
  /** Skills to bootstrap on conversation creation. */
  activeSkills?: string[]
  /** Scene agent payload for Drawer Chat. */
  sceneAgentPayload?: SceneAgentPayload | null
  /** Persisted scene metadata for scene-originated conversations. */
  sceneConversationMeta?: { sceneKey: string } | null

  // ── Full-mode callbacks (ChatPage passes these, Drawer does not) ──
  /** Called when conversation is auto-created (so ChatPage can track it). */
  onConversationCreated?: (conversation: Conversation) => void
  /** Called when skills change during streaming. */
  onSkillsChanged?: (skills: string[]) => void
  /** Called when datasource_id changes via context_delta. */
  onDatasourceContextChanged?: (datasourceId: number) => void
  /** Available datasources for tenant badge resolution. */
  datasources?: DataSource[]

  // ── Full-mode: managed conversation (ChatPage supplies this) ──
  /** If provided, the controller uses this conversation instead of auto-creating. */
  managedConversation?: Conversation | null
  /** If true, fetch messages + events when managedConversation changes. */
  fetchOnConversationChange?: boolean
  /** Reset local conversation state when this key changes. */
  freshSessionKey?: string | null

  /** Optional builder scope; creates a build session for this conversation. */
  builderScope?: BuilderScopeConfig
  /** Optional serializer for builder conversation context. */
  conversationContextBuilder?: (messages: Message[], nextInput: string) => string | undefined

  // ── Custom stream mode (FunctionBuildPage uses this) ──
  /** Replace chatApi.stream with a custom function. Skips conversation management. */
  customSendFn?: (content: string, signal: AbortSignal) => Promise<Response>
  /** Called after stream completes successfully with the donePayload. */
  onStreamDone?: (donePayload: Record<string, unknown>) => void
}

export type ChatControllerReturn = {
  // Core state
  input: string
  setInput: (value: string) => void
  messages: Message[]
  streamingParts: ContentPart[]
  streamingAgentName: string
  streaming: boolean
  runtimeStatus: RuntimeStatus | null
  contextStatus: ChatContextStatus | null
  contextCompressionNotice: ContextCompressionNotice | null
  conversationId: number | null

  // Pending actions
  pendingActions: PendingAction[]
  currentBatchPendingActions: PendingAction[]
  pendingActionByToken: Map<string, PendingAction>
  processingActionToken: string | null

  // Save-as-agent
  showReuseSuggestion: boolean
  setShowReuseSuggestion: (value: boolean) => void
  saveAgentState: SaveAgentState | null
  setSaveAgentState: (value: SaveAgentState | null) => void
  savingAgent: boolean
  handleSaveAsAgent: () => Promise<void>

  // Active skills
  activeSkills: string[]

  // Actions
  sendMessage: (override?: string, options?: { runDatasourceIds?: number[] }) => Promise<void>
  stopMessage: () => void
  confirmPendingAction: (token: string) => Promise<void>
  cancelPendingAction: (token: string) => Promise<void>
  confirmCurrentBatch: () => Promise<void>
  cancelCurrentBatch: () => Promise<void>

  // NL command helpers
  isConfirmCommand: (text: string) => boolean
  isCancelCommand: (text: string) => boolean

  // Fetch (for ChatPage post-stream refresh)
  fetchMessages: (conversationId: number, options?: { finalizeStream?: boolean }) => Promise<boolean>
  refreshPendingActions: (conversationId: number) => Promise<void>

  // External message control (custom stream mode)
  appendMessage: (role: string, content: string) => void
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>

  // Handoff
  handoff: ChatHandoff | null
  setHandoff: (value: ChatHandoff | null) => void
  loadingHandoff: boolean
}

// ── Utility functions ──────────────────────────────────────────────────

function isAbortError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false
  const payload = error as Record<string, unknown>
  const name = typeof payload.name === "string" ? payload.name : ""
  if (name === "AbortError") return true
  const message = typeof payload.message === "string" ? payload.message.toLowerCase() : ""
  return message.includes("aborted")
}

function extractStrategyReasonCode(payload: unknown): string {
  if (!payload || typeof payload !== "object") return ""
  const data = payload as Record<string, unknown>
  const raw = data.strategy_reason_code
  return typeof raw === "string" ? raw.trim().toLowerCase() : ""
}

function shouldShowReuseSuggestionFromEvents(events: ChatEvent[]): boolean {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.event_type === "agent_save") {
      const phase = String(event.phase || "").trim().toLowerCase()
      const payload =
        event.payload && typeof event.payload === "object"
          ? (event.payload as Record<string, unknown>)
          : null
      const stage = typeof payload?.stage === "string" ? payload.stage.trim().toLowerCase() : ""
      if (phase === "done" || stage === "completed") return false
    }
    if (event.event_type !== "reflect") continue
    const reasonCode = extractStrategyReasonCode(event.payload)
    if (reasonCode === "reuse" || reasonCode === "extend") return true
    if (reasonCode === "create") return false
  }
  return false
}

function extractLatestActiveSkills(events: ChatEvent[]): string[] | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.event_type !== "skill_delta" || !event.payload || typeof event.payload !== "object") continue
    const payload = event.payload as Record<string, unknown>
    if (!Array.isArray(payload.active_skills)) continue
    return payload.active_skills
      .filter((item): item is string => typeof item === "string")
      .map((item) => item.trim())
      .filter(Boolean)
  }
  return null
}

function normalizeRuntimeAgentName(agentName?: string): string {
  const normalized = String(agentName || "").trim()
  if (!normalized || normalized === "Assistant" || normalized === "Runtime") return ""
  return normalized
}

function toRuntimeStatusPhase(name: RuntimeCoreEvent["name"]): RuntimeStatus["phase"] {
  if (name === "plan") return "plan"
  if (name === "act" || name === "observe") return "tool"
  return "reflect"
}

function normalizeActionCommand(text: string): string {
  return text.replace(/\s+/g, "").toLowerCase()
}

function checkIsConfirmCommand(text: string): boolean {
  return /^(确认|确认执行|执行|继续|同意|ok|yes|y)$/.test(normalizeActionCommand(text))
}

function checkIsCancelCommand(text: string): boolean {
  return /^(取消|不执行|放弃|算了|no|n)$/.test(normalizeActionCommand(text))
}

function parseToolArgs(argumentsText?: string): Record<string, unknown> | null {
  if (!argumentsText) return null
  try {
    const parsed = JSON.parse(argumentsText)
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function hydrateMessagesWithToolEvents(messages: Message[], events: ChatEvent[]): Message[] {
  const persistedText = new Set<string>()
  const persistedToolIds = new Set<string>()
  for (const message of messages) {
    if (message.role !== "assistant") continue
    const content = message.content.trim()
    if (content) persistedText.add(content)
    for (const part of message.content_parts ?? []) {
      if ((part.type === "text" || part.type === "progress") && part.text.trim()) {
        persistedText.add(part.text.trim())
      } else if (part.type === "tool_use" && part.id) {
        persistedToolIds.add(part.id)
      }
    }
    for (const toolCall of message.tool_calls ?? []) {
      if (toolCall.id) persistedToolIds.add(toolCall.id)
    }
  }
  const assistantMessages = events
    .filter((event) => event.event_type === "assistant" || event.event_type === "assistant_progress")
    .map((event): Message | null => {
      const payload = event.payload
      if (!payload || typeof payload !== "object") return null
      const content = String(payload.content || payload.text || "").trim()
      if (!content || persistedText.has(content)) return null
      persistedText.add(content)
      return {
        id: -2_000_000 - event.id,
        conversation_id: event.conversation_id,
        role: "assistant",
        content,
        agent_name: event.agent_name,
        created_at: event.created_at,
      }
    })
    .filter((message): message is Message => message !== null)

  const toolMessages = events
    .filter((event) => event.event_type === "step_result" || event.event_type === "tool_result")
    .map((event): Message | null => {
      const payload = event.payload
      if (!payload || typeof payload !== "object") return null
      const name = String(payload.name || payload.tool_name || "").trim()
      if (!name) return null
      const toolCallId = String(payload.tool_call_id || payload.step_id || `event-${event.id}`)
      if (persistedToolIds.has(toolCallId)) return null
      const input = typeof payload.input === "object" && payload.input
        ? payload.input as Record<string, unknown>
        : parseToolArgs(typeof payload.arguments === "string" ? payload.arguments : undefined) ?? {}
      const result = payload.result
      const resultData = result && typeof result === "object"
        ? (result as Record<string, unknown>).data
        : undefined
      const resultRecord = resultData && typeof resultData === "object"
        ? resultData as Record<string, unknown>
        : {}
      const pendingToken = typeof resultRecord.action_token === "string" ? resultRecord.action_token : null
      return {
        id: -1_000_000 - event.id,
        conversation_id: event.conversation_id,
        role: "assistant",
        content: "",
        content_parts: [{
          type: "tool_use",
          id: toolCallId,
          name,
          input,
          result,
          pending_action_token: pendingToken,
          pending_action_status: pendingToken ? "pending" : null,
        }],
        created_at: event.created_at,
      }
    })
    .filter((message): message is Message => message !== null)

  return [...messages, ...assistantMessages, ...toolMessages].sort((left, right) => {
    const timeDelta = Date.parse(left.created_at) - Date.parse(right.created_at)
    if (Number.isFinite(timeDelta) && timeDelta !== 0) return timeDelta
    return left.id - right.id
  })
}

function parseNumericId(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}


export function getTenantBadge(
  toolName: string | undefined,
  argumentsText: string | undefined,
  result: unknown,
  datasources: DataSource[]
): TenantBadge | null {
  if (toolName !== "execute_sql" && toolName !== "explain_sql") return null
  const parsedArgs = parseToolArgs(argumentsText)
  const resultPayload = result && typeof result === "object" ? (result as Record<string, unknown>) : null
  const resultData =
    resultPayload && resultPayload.data && typeof resultPayload.data === "object"
      ? (resultPayload.data as Record<string, unknown>)
      : null

  const resolvedRole = String(resultData?.resolved_role ?? parsedArgs?.role ?? "user").toLowerCase()
  if (resolvedRole === "sys") return { text: "sys", isSys: true }
  if (resolvedRole === "user" || resolvedRole === "business" || resolvedRole === "tenant") {
    return { text: "user", isSys: false }
  }

  const datasourceId =
    parseNumericId(resultData?.resolved_datasource_id) ?? parseNumericId(parsedArgs?.datasource_id)
  const matchedDatasource =
    datasourceId !== null ? datasources.find((item) => item.id === datasourceId) : undefined
  if (matchedDatasource?.tenant_role === "sys") return { text: "sys", isSys: true }
  return { text: "user", isSys: false }
}

// ── Hook ───────────────────────────────────────────────────────────────

export function useChatController({
  title,
  datasourceId,
  activeSkills: initialActiveSkills,
  sceneAgentPayload,
  sceneConversationMeta,
  onConversationCreated,
  onSkillsChanged,
  onDatasourceContextChanged,
  managedConversation,
  fetchOnConversationChange = false,
  freshSessionKey,
  builderScope,
  conversationContextBuilder,
  customSendFn,
  onStreamDone,
}: ChatControllerParams): ChatControllerReturn {
  const { locale, t } = useShellI18n()
  // ── Core state ──
  const [internalConversationId, setInternalConversationId] = useState<number | null>(null)
  const conversationId = managedConversation?.id ?? internalConversationId
  const [input, setInput] = useState("")
  const [messages, setMessages] = useState<Message[]>([])
  const [runtimeAgentName, setRuntimeAgentName] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null)
  const [contextStatus, setContextStatus] = useState<ChatContextStatus | null>(null)
  const [contextCompressionNotice, setContextCompressionNotice] = useState<ContextCompressionNotice | null>(null)
  // streamingParts is the live content accumulator during streaming.
  // It is exposed to ChatThreadView so that the streaming message can be rendered
  // as a separate overlay that never appears in `messages`, avoiding the
  // key-change race that causes tapClientLookup index-out-of-bounds errors.
  const [streamingParts, setStreamingParts] = useState<ContentPart[]>([])

  // ── Pending actions ──
  const [pendingActions, setPendingActions] = useState<PendingAction[]>([])
  const [processingActionToken, setProcessingActionToken] = useState<string | null>(null)

  // ── Save-as-agent ──
  const [showReuseSuggestion, setShowReuseSuggestion] = useState(false)
  const [saveAgentState, setSaveAgentState] = useState<SaveAgentState | null>(null)
  const [savingAgent, setSavingAgent] = useState(false)

  // ── Active skills ──
  const [activeSkills, setActiveSkills] = useState<string[]>(initialActiveSkills ?? [])

  // ── Handoff ──
  const [handoff, setHandoff] = useState<ChatHandoff | null>(null)
  const [loadingHandoff] = useState(false)

  // ── Refs ──
  const abortRef = useRef<AbortController | null>(null)
  const abortRequestedRef = useRef(false)
  const fetchConversationIdRef = useRef<number | null>(null)
  const staleConversationResetRef = useRef<number | null>(null)
  const localMessageIdRef = useRef(0)
  const builderSessionRef = useRef<{ conversationId: number; sessionId: number } | null>(null)
  // Accumulates content_parts for the streaming sentinel message (id=-1 in messages).
  const streamingPartsRef = useRef<ContentPart[]>([])

  // ── Derived state ──
  const currentBatchPendingActions = useMemo<PendingAction[]>(() => {
    if (pendingActions.length === 0) return []
    const latest = pendingActions[pendingActions.length - 1]
    const latestBatchId = String(latest?.batch_id || "").trim()
    if (!latestBatchId) return pendingActions
    const batchActions = pendingActions.filter((item) => String(item.batch_id || "").trim() === latestBatchId)
    return batchActions.length > 0 ? batchActions : pendingActions
  }, [pendingActions])

  const pendingActionByToken = useMemo(() => {
    const map = new Map<string, PendingAction>()
    pendingActions.forEach((item) => {
      if (item.token) map.set(item.token, item)
    })
    return map
  }, [pendingActions])

  // ── Helpers ──

  // flushStreamingParts snapshots the current ref into state so React re-renders.
  // It does NOT touch `messages` — the streaming content is kept entirely separate
  // to avoid key-change collisions with real messages in the assistant-ui fiber tree.
  function flushStreamingParts() {
    setStreamingParts([...streamingPartsRef.current])
  }

  function nextLocalMessageId() {
    localMessageIdRef.current += 1
    return Date.now() * 1000 + localMessageIdRef.current
  }

  async function ensureBuilderSession(cid: number): Promise<boolean> {
    if (!builderScope) return true
    const ttlSeconds = builderScope.ttlSeconds ?? 1800
    const current = builderSessionRef.current
    try {
      if (current && current.conversationId === cid) {
        await conversationsApi.heartbeatBuildSession(cid, current.sessionId, ttlSeconds)
        return true
      }
      const session = await conversationsApi.createBuildSession(cid, {
        scope_object_type: builderScope.scopeObjectType,
        scope_object_id: builderScope.scopeObjectId,
        ttl_seconds: ttlSeconds,
      })
      builderSessionRef.current = { conversationId: cid, sessionId: session.id }
      return true
    } catch {
      toast.error("Failed to create build session")
      return false
    }
  }

  async function ensureConversation(): Promise<number | null> {
    if (conversationId) return conversationId
    const bootstrapSkills =
      initialActiveSkills && initialActiveSkills.length > 0 &&
      !(sceneAgentPayload?.skills && sceneAgentPayload.skills.length > 0)
        ? initialActiveSkills
        : undefined
    // Resolve datasource_id: explicit prop first, then fall back to scene_agent context
    const inferredDatasourceId: number | undefined = (() => {
      if (typeof datasourceId === "number" && datasourceId > 0) return datasourceId
      const ds = sceneAgentPayload?.context?.datasource
      if (ds && typeof ds === "object" && typeof (ds as Record<string, unknown>).id === "number") {
        return (ds as Record<string, unknown>).id as number
      }
      return undefined
    })()
    const basePayload: {
      title: string
      datasource_id?: number
      category?: "primary" | "scene"
      scene_key?: string | null
      read_only?: boolean
    } = {
      title,
      datasource_id: inferredDatasourceId ?? undefined,
      category: sceneConversationMeta ? "scene" : "primary",
      scene_key: sceneConversationMeta?.sceneKey ?? undefined,
      read_only: Boolean(sceneConversationMeta),
    }
    const payload =
      bootstrapSkills && bootstrapSkills.length > 0
        ? { ...basePayload, active_skills: bootstrapSkills }
        : basePayload

    // For scene conversations with a stable scene_key, look up an existing conversation
    // before creating a new one so that page refresh or drawer re-open restores history.
    const sceneKey = sceneConversationMeta?.sceneKey
    if (sceneKey) {
      try {
        const existing = await conversationsApi.list({ scene_key: sceneKey, category: "scene" })
        if (existing.length > 0) {
          const found = existing[0] // already ordered by updated_at desc
          const scopeReady = await ensureBuilderSession(found.id)
          if (!scopeReady) return null
          setInternalConversationId(found.id)
          if (fetchOnConversationChange) {
            await fetchMessagesImpl(found.id)
          }
          return found.id
        }
      } catch {
        // fall through to create
      }
    }

    try {
      let created: Conversation
      try {
        created = await conversationsApi.create(payload)
      } catch (error) {
        if (bootstrapSkills && bootstrapSkills.length > 0 && isUnknownActiveSkillsError(error)) {
          created = await conversationsApi.create(basePayload)
        } else {
          throw error
        }
      }
      const scopeReady = await ensureBuilderSession(created.id)
      if (!scopeReady) return null
      setInternalConversationId(created.id)
      onConversationCreated?.(created)
      return created.id
    } catch {
      toast.error("Failed to create conversation")
      return null
    }
  }

  // ── Data fetching ──

  async function fetchMessagesImpl(
    cid: number,
    options?: { finalizeStream?: boolean }
  ): Promise<boolean> {
    fetchConversationIdRef.current = cid
    staleConversationResetRef.current = null
    try {
      const [messageData, eventData, pendingData, contextData] = await Promise.all([
        messagesApi.list(cid),
        chatApi.listEvents(cid).catch(() => []),
        chatApi.listPendingActions(cid).catch(() => []),
        chatApi.getContextStatus(cid).catch(() => null),
      ])
      if (fetchConversationIdRef.current !== cid) return false
      if (options?.finalizeStream) setRuntimeStatus(null)
      if (options?.finalizeStream && messageData.length === 0 && eventData.length === 0) {
        setPendingActions(pendingData)
        return false
      }
      setMessages(hydrateMessagesWithToolEvents(messageData, eventData))
      setPendingActions(pendingData)
      setContextStatus(contextData)
      setShowReuseSuggestion(shouldShowReuseSuggestionFromEvents(eventData))
      const eventSkills = extractLatestActiveSkills(eventData)
      if (eventSkills) {
        setActiveSkills(eventSkills)
        onSkillsChanged?.(eventSkills)
        return true
      }
      const fallbackSkills = managedConversation?.id === cid ? managedConversation.active_skills ?? [] : []
      setActiveSkills(fallbackSkills)
      return true
    } catch (error: unknown) {
      const detail = extractApiErrorDetail(error)
      if (!managedConversation && sceneConversationMeta && isConversationMissingError(error, detail)) {
        staleConversationResetRef.current = cid
        const active = builderSessionRef.current
        if (active && active.conversationId === cid) {
          void conversationsApi.closeBuildSession(active.conversationId, active.sessionId).catch(() => {})
          builderSessionRef.current = null
        }
        fetchConversationIdRef.current = null
        setInternalConversationId(null)
        setMessages([])
        setRuntimeStatus(null)
        setContextStatus(null)
        setContextCompressionNotice(null)
        setPendingActions([])
        setProcessingActionToken(null)
        setShowReuseSuggestion(false)
        setSaveAgentState(null)
        setSavingAgent(false)
        setHandoff(null)
        toast.error(t("chat.toast.conversationExpired"))
        return false
      }
      toast.error(t("chat.toast.messagesLoadFailed"))
      return false
    }
  }

  async function refreshPendingActionsImpl(cid: number) {
    try {
      const actions = await chatApi.listPendingActions(cid)
      setPendingActions(actions)
    } catch {
      setPendingActions([])
    }
  }

  // ── Conversation change effect ──
  useEffect(() => {
    if (!fetchOnConversationChange) return
    const cid = managedConversation?.id
    if (cid) {
      setActiveSkills(managedConversation.active_skills ?? [])
      setShowReuseSuggestion(false)
      setRuntimeStatus(null)
      setContextCompressionNotice(null)
      setPendingActions([])
      setProcessingActionToken(null)
      setSavingAgent(false)
      setSaveAgentState(null)
      void fetchMessagesImpl(cid)
    } else {
      setMessages([])
      setRuntimeStatus(null)
      setContextStatus(null)
      setContextCompressionNotice(null)
      setPendingActions([])
      setProcessingActionToken(null)
      setSavingAgent(false)
      setSaveAgentState(null)
      setActiveSkills([])
      setShowReuseSuggestion(false)
      setHandoff(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [managedConversation?.id, fetchOnConversationChange])

  useEffect(() => {
    return () => {
      const active = builderSessionRef.current
      if (!active) return
      void conversationsApi.closeBuildSession(active.conversationId, active.sessionId).catch(() => {})
    }
  }, [])

  useEffect(() => {
    const active = builderSessionRef.current
    if (!active || managedConversation) return
    void conversationsApi.closeBuildSession(active.conversationId, active.sessionId).catch(() => {})
    builderSessionRef.current = null
    setInternalConversationId(null)
    setRuntimeStatus(null)
    setContextStatus(null)
    setContextCompressionNotice(null)
    setPendingActions([])
    setProcessingActionToken(null)
  }, [builderScope?.scopeObjectType, builderScope?.scopeObjectId, managedConversation])

  useEffect(() => {
    if (!freshSessionKey || managedConversation) return
    const active = builderSessionRef.current
    if (active) {
      void conversationsApi.closeBuildSession(active.conversationId, active.sessionId).catch(() => {})
      builderSessionRef.current = null
    }
    fetchConversationIdRef.current = null
    setInternalConversationId(null)
    setMessages([])
    setRuntimeStatus(null)
    setContextStatus(null)
    setContextCompressionNotice(null)
    setPendingActions([])
    setProcessingActionToken(null)
    setShowReuseSuggestion(false)
    setSaveAgentState(null)
    setSavingAgent(false)
    setHandoff(null)

    // Proactively restore an existing scene conversation so history is visible
    // immediately on drawer open, without waiting for the user to send a message.
    const sceneKey = sceneConversationMeta?.sceneKey
    if (!sceneKey) return
    void (async () => {
      try {
        const existing = await conversationsApi.list({ scene_key: sceneKey, category: "scene" })
        if (existing.length > 0) {
          const found = existing[0]
          setInternalConversationId(found.id)
          await fetchMessagesImpl(found.id)
        }
      } catch {
        // no-op: fresh session will be created on first send
      }
    })()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [freshSessionKey, managedConversation])

  // Reset pending actions when scene changes (Drawer Chat)
  useEffect(() => {
    setPendingActions([])
    setProcessingActionToken(null)
  }, [datasourceId, sceneAgentPayload?.key])

  // ── Pending action handlers ──

  async function confirmPendingActionImpl(token: string) {
    if (!conversationId || streaming || savingAgent || processingActionToken) return
    setProcessingActionToken(token)
    try {
      const response = await chatApi.confirmPendingAction(conversationId, token)
      if (response.should_resume) {
        await fetchMessagesImpl(conversationId, { finalizeStream: true })
        setProcessingActionToken(null)
        await sendMessageImpl(t("chat.action.resumeAfterFailure"))
      } else {
        toast.success("Action confirmed")
        await fetchMessagesImpl(conversationId, { finalizeStream: true })
      }
    } catch (error: unknown) {
      const detail = extractApiErrorDetail(error)
      toast.error(detail || "Failed to confirm action. Please refresh and try again.")
      await fetchMessagesImpl(conversationId, { finalizeStream: true })
      await refreshPendingActionsImpl(conversationId)
      setRuntimeStatus(null)
    } finally {
      setProcessingActionToken(null)
    }
  }

  async function cancelPendingActionImpl(token: string) {
    if (!conversationId || streaming || savingAgent || processingActionToken) return
    setProcessingActionToken(token)
    try {
      await chatApi.cancelPendingAction(conversationId, token)
      toast.success("Action cancelled")
      await fetchMessagesImpl(conversationId, { finalizeStream: true })
    } catch {
      toast.error("Failed to cancel. Please try again.")
    } finally {
      setProcessingActionToken(null)
    }
  }

  async function confirmCurrentBatchImpl() {
    if (!conversationId || streaming || savingAgent || processingActionToken || currentBatchPendingActions.length === 0) return
    setProcessingActionToken("__batch_confirm__")
    const tokens = currentBatchPendingActions.map((item) => item.token)
    try {
      let shouldResume = false
      for (const token of tokens) {
        const resp = await chatApi.confirmPendingAction(conversationId, token)
        if (resp.should_resume) shouldResume = true
      }
      await fetchMessagesImpl(conversationId, { finalizeStream: true })
      if (shouldResume) {
        setProcessingActionToken(null)
        await sendMessageImpl(t("chat.action.resumeAfterFailure"))
      } else {
        toast.success(`Confirmed ${tokens.length} action(s) in this batch`)
      }
    } catch (error: unknown) {
      const detail = extractApiErrorDetail(error)
      toast.error(detail || "Failed to confirm batch. Please refresh and try again.")
      await fetchMessagesImpl(conversationId, { finalizeStream: true })
      await refreshPendingActionsImpl(conversationId)
      setRuntimeStatus(null)
    } finally {
      setProcessingActionToken(null)
    }
  }

  async function cancelCurrentBatchImpl() {
    if (!conversationId || streaming || savingAgent || processingActionToken || currentBatchPendingActions.length === 0) return
    setProcessingActionToken("__batch_cancel__")
    const tokens = currentBatchPendingActions.map((item) => item.token)
    try {
      for (const token of tokens) {
        await chatApi.cancelPendingAction(conversationId, token)
      }
      toast.success(`Cancelled ${tokens.length} pending action(s) in this batch`)
      await fetchMessagesImpl(conversationId, { finalizeStream: true })
    } catch {
      toast.error("Failed to cancel batch. Please try again.")
      await refreshPendingActionsImpl(conversationId)
    } finally {
      setProcessingActionToken(null)
    }
  }

  // ── Save as Agent ──

  async function handleSaveAsAgentImpl() {
    const cid = conversationId
    if (!cid || streaming || savingAgent) return
    const hasContent = messages.length > 0
    if (!hasContent) {
      toast.error("No summarizable content in the current conversation. Please complete a round of dialogue first.")
      return
    }
    setSavingAgent(true)
    setShowReuseSuggestion(false)
    setSaveAgentState({ stage: "summarizing", text: "Summarizing context..." })

    try {
      const response = await chatApi.saveAgentStream(cid)
      if (!response.ok || !response.body) throw new Error("Save process ended unexpectedly. Please try again.")

      const reader = response.body.getReader()
      const decoder = new TextDecoder("utf-8")
      let buffer = ""
      let streamDone = false

      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() ?? ""

        for (const rawLine of lines) {
          const line = rawLine.trim()
          if (!line) continue

          // Parse VDS data channel (2:) or finish (d:)
          const colonIdx = line.indexOf(":")
          if (colonIdx < 0) continue
          const code = line.slice(0, colonIdx)

          if (code === "2") {
            let items: unknown[]
            try { items = JSON.parse(line.slice(colonIdx + 1)) as unknown[] } catch { continue }
            for (const item of items) {
              if (!item || typeof item !== "object") continue
              const event = item as SaveAgentStreamEvent
              if (event.type === "save_agent_status") {
                const stage = String(event.data?.stage || "")
                setSaveAgentState({
                  stage: stage === "saving_agent" ? "saving" : "summarizing",
                  text: typeof event.data?.message === "string" ? event.data.message : (stage === "saving_agent" ? "Saving Agent..." : "Summarizing context..."),
                })
                continue
              }
              if (event.type === "save_agent_done") {
                setSaveAgentState({
                  stage: "done",
                  text: typeof event.data?.message === "string" ? event.data.message : "Saved. You can go to the Agent edit page to continue editing.",
                  agentId: typeof event.data?.agent_id === "number" ? event.data.agent_id : undefined,
                  agentName: typeof event.data?.agent_name === "string" ? event.data.agent_name : undefined,
                  agentUrl: typeof event.data?.agent_url === "string" ? event.data.agent_url : undefined,
                })
                continue
              }
              if (event.type === "error") {
                const message = typeof event.data?.user_message === "string" && event.data.user_message.trim()
                  ? event.data.user_message.trim()
                  : "Failed to save Agent. Please try again."
                throw new Error(message)
              }
              if (event.type === "done") { streamDone = true; break outer }
            }
          } else if (code === "d") {
            streamDone = true
            break outer
          }

          await new Promise((r) => setTimeout(r, 0))
        }
      }

      if (!streamDone) throw new Error("Save process ended unexpectedly. Please try again.")
      await fetchMessagesImpl(cid)
      setShowReuseSuggestion(false)
    } catch (error: unknown) {
      const message = typeof (error as Error)?.message === "string" ? (error as Error).message : "Failed to save Agent. Please try again."
      setSaveAgentState({ stage: "error", text: message })
      toast.error(message)
    } finally {
      setSavingAgent(false)
    }
  }

  // ── Main send ──

  async function sendMessageImpl(override?: string, options?: { runDatasourceIds?: number[] }) {
    const text = (override ?? input).trim()
    if (!text || streaming || savingAgent) return

    // NL command interception
    if (pendingActions.length > 0 && checkIsConfirmCommand(text)) {
      setInput("")
      await confirmCurrentBatchImpl()
      return
    }
    if (pendingActions.length > 0 && checkIsCancelCommand(text)) {
      setInput("")
      await cancelCurrentBatchImpl()
      return
    }

    const isCustomStream = Boolean(customSendFn)
    const cid = isCustomStream ? (conversationId ?? 0) : await ensureConversation()
    if (!isCustomStream && !cid) return
    if (!isCustomStream) {
      const scopeReady = await ensureBuilderSession(cid!)
      if (!scopeReady) return
    }

    setInput("")
    setStreaming(true)
    setContextCompressionNotice(null)
    setRuntimeAgentName("")
    streamingPartsRef.current = []
    setStreamingParts([])
    setRuntimeStatus({ phase: "thinking", text: "Analyzing request..." })
    setProcessingActionToken(null)
    abortRequestedRef.current = false
    const controller = new AbortController()
    abortRef.current = controller

    let saveAgentIntentInStream = false
    let fullText = ""
    let capturedAgentName = ""
    const activeHandoffId =
      !isCustomStream && handoff && handoff.status === "pending" && handoff.conversation_id === cid
        ? handoff.id
        : undefined

    try {
      // Add user message
      if (isCustomStream) {
        setMessages((prev) => [...prev, {
          id: nextLocalMessageId(),
          conversation_id: cid!,
          role: "user",
          content: text,
          created_at: new Date().toISOString(),
        }])
      } else {
        const userMessage = await messagesApi.create({
          conversation_id: cid!,
          role: "user",
          content: text,
        })
        setMessages((prev) => [...prev, userMessage])
      }

      let response: Response
      streamingPartsRef.current = []
      if (customSendFn) {
        response = await customSendFn(text, controller.signal)
      } else {
        const runDatasourceIds = options?.runDatasourceIds ??
          (typeof datasourceId === "number" && datasourceId > 0 ? [datasourceId] : undefined)
        const conversationContext = conversationContextBuilder?.(messages, text)
        response = await chatApi.stream(cid!, text, {
          signal: controller.signal,
          runDatasourceIds,
          handoffId: activeHandoffId,
          sceneAgent: sceneAgentPayload || undefined,
          conversationContext,
          locale,
        })
      }

      if (activeHandoffId) {
        setHandoff(null)
      }

      const sseStreamResult = await consumeVds(response, {
        onEvent: (normalized: RuntimeNormalizedEvent, raw?: Record<string, unknown>) => {
          const eventType = String(raw?.type || "").trim().toLowerCase()

          // ── Skill delta ──
          if (normalized.kind === "skill_delta") {
            setActiveSkills(normalized.active_skills)
            onSkillsChanged?.(normalized.active_skills)
            return
          }

          // ── Save-agent inline events ──
          if (eventType === "save_agent_status") {
            saveAgentIntentInStream = true
            setShowReuseSuggestion(false)
            setRuntimeStatus(null)
            const rawData = raw?.data && typeof raw.data === "object" ? (raw.data as Record<string, unknown>) : {}
            const stage = String(rawData.stage || "")
            const msg = rawData.message
            setSaveAgentState({
              stage: stage === "saving_agent" ? "saving" : "summarizing",
              text: typeof msg === "string" ? msg : (stage === "saving_agent" ? "Saving Agent..." : "Summarizing context..."),
            })
            return
          }
          if (eventType === "save_agent_done") {
            saveAgentIntentInStream = true
            setShowReuseSuggestion(false)
            setRuntimeStatus(null)
            const d = raw?.data && typeof raw.data === "object" ? (raw.data as Record<string, unknown>) : {}
            setSaveAgentState({
              stage: "done",
              text: typeof d.message === "string" ? d.message : "Saved. You can go to the Agent edit page to continue editing.",
              agentId: typeof d.agent_id === "number" ? d.agent_id : undefined,
              agentName: typeof d.agent_name === "string" ? d.agent_name : undefined,
              agentUrl: typeof d.agent_url === "string" ? d.agent_url : undefined,
            })
            return
          }

          // ── User-visible work narration ──
          if (normalized.kind === "extension" && normalized.name === "assistant_progress") {
            const d = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (normalized as RuntimeExtensionEvent).payload
            const progressText = String(d.text || normalized.summary || "").trim()
            const stage = String(d.stage || "working")
            if (progressText) {
              setRuntimeStatus({
                phase: stage === "planning" || stage === "acting" ? "plan" : "reflect",
                text: progressText,
              })
              const lastPart = streamingPartsRef.current[streamingPartsRef.current.length - 1]
              if (!(lastPart?.type === "progress" && lastPart.text === progressText)) {
                streamingPartsRef.current.push({
                  type: "progress",
                  text: progressText,
                  stage,
                })
                flushStreamingParts()
              }
            }
            return
          }

          // ── Long-running task state ──
          if (eventType === "task_contract") {
            setRuntimeStatus({ phase: "plan", text: "Task acceptance criteria established. Planning execution..." })
            return
          }
          if (eventType === "progress") {
            const d = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object" ? raw : {})
            const decision = String(d.decision || "")
            const reason = typeof d.reason === "string" ? d.reason : ""
            if (decision === "recoverable_failure" || decision === "transient_failure") {
              setRuntimeStatus({ phase: "reflect", text: reason || "Recoverable failure detected. Adjusting strategy..." })
            } else if (decision === "await_confirmation") {
              setRuntimeStatus({ phase: "reflect", text: reason || "Waiting for authorization or confirmation..." })
            } else if (decision === "blocked" || decision === "stalled") {
              setRuntimeStatus({ phase: "reflect", text: reason || "Execution is blocked. A checkpoint has been saved." })
            } else if (decision === "candidate_complete") {
              setRuntimeStatus({ phase: "reflect", text: "Candidate answer ready. Verifying acceptance criteria..." })
            } else {
              setRuntimeStatus({ phase: "reflect", text: reason || "New evidence recorded. Continuing..." })
            }
            return
          }
          if (eventType === "verification") {
            const d = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object" ? raw : {})
            const satisfied = Boolean(d.satisfied)
            setRuntimeStatus({
              phase: "reflect",
              text: satisfied
                ? "Acceptance criteria verified. Preparing final response..."
                : "Advisory quality review recorded. Preparing final response...",
            })
            return
          }
          if (eventType === "checkpoint") {
            const d = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object" ? raw : {})
            const reason = typeof d.reason === "string" ? d.reason : ""
            setRuntimeStatus({ phase: "reflect", text: reason || "Progress checkpoint saved. You can resume this task." })
            return
          }
          if (eventType === "context_status") {
            const data = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object" ? raw : null)
            const parsed = parseContextStatus(data)
            if (parsed) setContextStatus(parsed)
            return
          }
          if (eventType === "context_compressed") {
            const data = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object" ? raw : null)
            const parsed = parseCompressionNotice(data)
            if (parsed) setContextCompressionNotice(parsed)
            return
          }
          if (eventType === "task_state") return

          // ── Core events (plan / act / observe / reflect / retry) ──
          if (
            normalized.kind === "core" &&
            (normalized.name === "plan" || normalized.name === "act" || normalized.name === "observe" || normalized.name === "reflect" || normalized.name === "retry")
          ) {
            const eventData = raw?.data && typeof raw.data === "object" ? (raw.data as Record<string, unknown>) : {}
            if (normalized.name === "reflect" && String(eventData.action || "") === "await_confirmation") {
              setRuntimeStatus({ phase: "reflect", text: "A change confirmation card has been generated. Please confirm before executing write operations." })
            } else {
              setRuntimeStatus({
                phase: toRuntimeStatusPhase(normalized.name),
                text: formatRuntimeCoreMessage(normalized, { includeAgent: false }).trim() || "Processing...",
              })
            }
            return
          }

          if (normalized.kind === "core" && normalized.name === "error") {
            setRuntimeStatus({ phase: "reflect", text: formatRuntimeCoreMessage(normalized) || "Processing failed" })
            return
          }

          // ── Done ──
          if (normalized.kind === "core" && normalized.name === "done") {
            const d = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object" ? raw : {})
            const status = String(d.status || "")
            setRuntimeStatus(
              status && status !== "completed"
                ? { phase: "reflect", text: "I’m holding back a final conclusion until the remaining evidence gaps are resolved." }
                : null
            )
            return
          }

          // ── Tool start ──
          if (normalized.kind === "extension" && (normalized.name === "tool_start" || eventType === "step_start")) {
            const d = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object"
                  ? (raw as Record<string, unknown>)
                  : (normalized.kind === "extension" ? (normalized as RuntimeExtensionEvent).payload : {}))
            const stepKind = typeof d.kind === "string" ? String(d.kind) : (normalized.name === "tool_start" || normalized.name === "tool_result" ? "tool" : "")
            // tool_start events use tool_call_id; step_start events use step_id
            const stepId = String(d.tool_call_id || d.step_id || "")
            const stepName = typeof d.name === "string" ? String(d.name) : ""
            const stepArgs = typeof d.arguments === "string" ? String(d.arguments) : ""
            const stepMessage = typeof d.message === "string" ? String(d.message) : ""

            // Context delta handling
            const contextDelta = d.context_delta && typeof d.context_delta === "object" ? (d.context_delta as Record<string, unknown>) : null
            const nextDatasourceId = contextDelta && typeof contextDelta.datasource_id === "number" ? contextDelta.datasource_id : null
            if (nextDatasourceId) onDatasourceContextChanged?.(nextDatasourceId)

            setRuntimeStatus({
              phase: stepKind === "workflow" ? "plan" : "tool",
              text: stepMessage || (stepName ? `Executing ${stepName}...` : "Executing step..."),
            })
            if (stepKind !== "tool" && stepKind !== "action") return

            const existingIdx = streamingPartsRef.current.findIndex(
              (p) => p.type === "tool_use" && stepId && p.id === stepId
            )
            if (existingIdx >= 0) {
              const existing = streamingPartsRef.current[existingIdx] as Extract<ContentPart, { type: "tool_use" }>
              streamingPartsRef.current[existingIdx] = {
                ...existing,
                name: stepName || existing.name,
                input: parseToolArgs(stepArgs) ?? existing.input,
              }
            } else {
              streamingPartsRef.current.push({
                type: "tool_use",
                id: stepId || `tool-${Date.now()}`,
                name: stepName,
                input: parseToolArgs(stepArgs) ?? {},
                result: undefined,
              })
            }
            flushStreamingParts()
            return
          }

          // ── Tool result ──
          if (normalized.kind === "extension" && (normalized.name === "tool_result" || eventType === "step_result")) {
            const d = raw?.data && typeof raw.data === "object"
              ? (raw.data as Record<string, unknown>)
              : (raw && typeof raw === "object"
                  ? (raw as Record<string, unknown>)
                  : (normalized.kind === "extension" ? (normalized as RuntimeExtensionEvent).payload : {}))
            const stepKind = typeof d.kind === "string" ? String(d.kind) : (normalized.name === "tool_start" || normalized.name === "tool_result" ? "tool" : "")
            // tool_result events use tool_call_id; step_result events use step_id
            const stepId = String(d.tool_call_id || d.step_id || "")
            const stepName = typeof d.name === "string" ? String(d.name) : ""
            const stepArgs = typeof d.arguments === "string" ? String(d.arguments) : ""
            const stepResult = d.result
            const stepMessage = typeof d.message === "string" ? String(d.message) : ""

            const contextDelta = d.context_delta && typeof d.context_delta === "object" ? (d.context_delta as Record<string, unknown>) : null
            const nextDatasourceId = contextDelta && typeof contextDelta.datasource_id === "number" ? contextDelta.datasource_id : null
            if (nextDatasourceId) onDatasourceContextChanged?.(nextDatasourceId)

            const requiresConfirmation = Boolean(
              stepResult && typeof stepResult === "object" && (stepResult as Record<string, unknown>).data &&
              typeof (stepResult as Record<string, unknown>).data === "object" &&
              ((stepResult as Record<string, unknown>).data as Record<string, unknown>).requires_confirmation
            )
            setRuntimeStatus(
              requiresConfirmation
                ? { phase: "reflect", text: "A change confirmation card has been generated. Please confirm before executing write operations." }
                : { phase: "reflect", text: stepMessage || "Read-only check complete. Analyzing results..." }
            )
            if (stepKind !== "tool" && stepKind !== "action") return

            const existingIdx = streamingPartsRef.current.findIndex(
              (p) => p.type === "tool_use" && stepId && p.id === stepId
            )
            if (existingIdx >= 0) {
              const existing = streamingPartsRef.current[existingIdx] as Extract<ContentPart, { type: "tool_use" }>
              streamingPartsRef.current[existingIdx] = {
                ...existing,
                name: stepName || existing.name,
                input: parseToolArgs(stepArgs) ?? existing.input,
                result: stepResult,
              }
            } else {
              streamingPartsRef.current.push({
                type: "tool_use",
                id: stepId || `tool-${Date.now()}`,
                name: stepName,
                input: parseToolArgs(stepArgs) ?? {},
                result: stepResult,
              })
            }
            flushStreamingParts()
            // Pending actions are refreshed once after the stream settles. Doing
            // it here as well races the final refresh and can briefly show stale
            // confirmation state.
            return
          }

          if (normalized.kind === "extension" && normalized.name === "verify_result") {
            const summary = normalized.summary || "Verify · Business result verification complete"
            setRuntimeStatus({ phase: "reflect", text: summary })
            return
          }

          // ── Reflect (reuse detection) ──
          if (eventType === "reflect" || (normalized.kind === "core" && normalized.name === "reflect")) {
            const d = raw?.data && typeof raw.data === "object" ? (raw.data as Record<string, unknown>) : {}
            const reasonCode = extractStrategyReasonCode(d)
            if (reasonCode === "reuse" || reasonCode === "extend") setShowReuseSuggestion(true)
            if (reasonCode === "create") setShowReuseSuggestion(false)
            return
          }

          // ── Assistant text ──
          if (normalized.kind === "assistant") {
            setRuntimeStatus(null)
            const displayAgentName = normalizeRuntimeAgentName(normalized.agent)
            if (displayAgentName) {
              capturedAgentName = displayAgentName
              setRuntimeAgentName(displayAgentName)
            }
            fullText += normalized.text
            const parts = streamingPartsRef.current
            const last = parts[parts.length - 1]
            if (last?.type === "text") {
              last.text += normalized.text
            } else {
              parts.push({ type: "text", text: normalized.text })
            }
            flushStreamingParts()
            return
          }

          // ── Error events ──
          // Save-agent errors
          if (eventType === "error") {
            const d = raw?.data && typeof raw.data === "object" ? (raw.data as Record<string, unknown>) : {}
            const errorClass = String(d.error_class || "")
            if (errorClass === "save_agent_error") {
              saveAgentIntentInStream = true
              const userMessage = typeof d.user_message === "string" ? d.user_message : "Failed to save Agent. Please try again."
              setSaveAgentState({ stage: "error", text: userMessage })
            }
            // consumeVds emits core error as terminalError, so this path is for data-channel error events
          }
        },
      })

      // ── Post-stream ──
      if (sseStreamResult.donePayload) {
        onStreamDone?.(sseStreamResult.donePayload)
      }

      const streamTerminalError = sseStreamResult.terminalError
      if (!fullText.trim() && !saveAgentIntentInStream && !streamTerminalError) {
        fullText = "No model response received. Please try again. If the issue persists, check the model service status."
        streamingPartsRef.current = [{ type: "text", text: fullText }]
        flushStreamingParts()
      }

      // Refresh from API (full mode gets full refresh; custom/embedded mode adds locally)
      if (!isCustomStream && fetchOnConversationChange) {
        // Clear the streaming overlay before fetching real messages so there is no
        // render frame where both the overlay (id="streaming") and the real persisted
        // message (id="m-N") coexist. That coexistence followed by overlay removal
        // shrinks the content array and triggers tapClientLookup index OOB in
        // assistant-ui's ContentPartRuntime fibers.
        const completedParts = [...streamingPartsRef.current]
        streamingPartsRef.current = []
        setStreamingParts([])
        const refreshOk = await fetchMessagesImpl(cid!, { finalizeStream: true })
        if (!refreshOk) {
          const fallbackText = streamTerminalError
            ? `${t("chat.error.prefix")}${streamTerminalError}`
            : fullText.trim() || "No model response received. Please try again. If the issue persists, check the model service status."
          setMessages((prev) => [...prev, {
            id: nextLocalMessageId(),
            conversation_id: cid!,
            role: "assistant",
            content: fallbackText,
            content_parts: completedParts.length > 0 ? completedParts : undefined,
            agent_name: capturedAgentName || undefined,
            created_at: new Date().toISOString(),
          }])
        } else if (streamTerminalError) {
          setMessages((prev) => {
            const lastMsg = prev[prev.length - 1]
            if (lastMsg && lastMsg.role === "assistant") return prev
            return [
              ...prev,
              {
                id: nextLocalMessageId(),
                conversation_id: cid!,
                role: "assistant",
                content: `${t("chat.error.prefix")}${streamTerminalError}`,
                created_at: new Date().toISOString(),
              },
            ]
          })
        }
      } else {
        // Embedded / custom stream mode: add message locally
        const finalText = fullText.trim()
        setMessages((prev) => {
          if (finalText) {
            return [...prev, {
              id: nextLocalMessageId(),
              conversation_id: cid ?? 0,
              role: "assistant",
              content: finalText,
              agent_name: capturedAgentName || undefined,
              created_at: new Date().toISOString(),
            }]
          } else if (!saveAgentIntentInStream) {
            return [...prev, {
              id: nextLocalMessageId(),
              conversation_id: cid ?? 0,
              role: "status" as string,
              content: "No valid analysis result returned. Please try again.",
              created_at: new Date().toISOString(),
            }]
          }
          return prev
        })
        setRuntimeStatus(null)
        if (!isCustomStream && cid) await refreshPendingActionsImpl(cid)
      }
    } catch (error: unknown) {
      if (abortRequestedRef.current || isAbortError(error)) {
        // Preserve any streamed text as a local message so the user sees it after abort
        const finalText = fullText.trim()
        if (finalText) {
          setMessages((prev) => [...prev, {
            id: nextLocalMessageId(),
            conversation_id: cid ?? 0,
            role: "assistant",
            content: finalText,
            agent_name: capturedAgentName || undefined,
            created_at: new Date().toISOString(),
          }])
        }
        setRuntimeStatus(null)
        return
      }
      const message = typeof (error as Error)?.message === "string" ? (error as Error).message : "Unknown error"
      if (saveAgentIntentInStream) {
        setRuntimeStatus(null)
        toast.error(message)
        return
      }
      setRuntimeStatus(null)
      if (!isCustomStream && fetchOnConversationChange && cid) {
        const refreshOk = await fetchMessagesImpl(cid, { finalizeStream: true })
        const conversationReset = staleConversationResetRef.current === cid
        staleConversationResetRef.current = null
        if (conversationReset) {
          return
        }
        if (refreshOk) {
          setMessages((prev) => {
            const lastMsg = prev[prev.length - 1]
            if (lastMsg && lastMsg.role === "assistant") return prev
            return [
              ...prev,
              {
                id: nextLocalMessageId(),
                conversation_id: cid,
                role: "assistant",
                content: `${t("chat.error.prefix")}${message}`,
                created_at: new Date().toISOString(),
              },
            ]
          })
        } else {
          setMessages((prev) => [
            ...prev,
            {
              id: nextLocalMessageId(),
              conversation_id: cid,
              role: "assistant",
              content: `${t("chat.error.prefix")}${message}`,
              created_at: new Date().toISOString(),
            },
          ])
        }
      } else {
        setMessages((prev) => [...prev, {
          id: nextLocalMessageId(),
          conversation_id: conversationId ?? 0,
          role: "status" as string,
          content: message,
          created_at: new Date().toISOString(),
        }])
      }
    } finally {
      streamingPartsRef.current = []
      setStreamingParts([])
      abortRequestedRef.current = false
      setStreaming(false)
    }
  }

  function stopMessageImpl() {
    if (!streaming) return
    abortRequestedRef.current = true
    setRuntimeStatus({ phase: "reflect", text: "Stopping generation..." })
    abortRef.current?.abort()
  }

  function appendMessageImpl(role: string, content: string) {
    setMessages((prev) => [...prev, {
      id: nextLocalMessageId(),
      conversation_id: conversationId ?? 0,
      role,
      content,
      created_at: new Date().toISOString(),
    }])
  }

  return {
    input,
    setInput,
    messages,
    streamingParts,
    streamingAgentName: runtimeAgentName,
    streaming,
    runtimeStatus,
    contextStatus,
    contextCompressionNotice,
    conversationId,
    pendingActions,
    currentBatchPendingActions,
    pendingActionByToken,
    processingActionToken,
    showReuseSuggestion,
    setShowReuseSuggestion,
    saveAgentState,
    setSaveAgentState,
    savingAgent,
    handleSaveAsAgent: handleSaveAsAgentImpl,
    activeSkills,
    sendMessage: sendMessageImpl,
    stopMessage: stopMessageImpl,
    appendMessage: appendMessageImpl,
    setMessages,
    confirmPendingAction: confirmPendingActionImpl,
    cancelPendingAction: cancelPendingActionImpl,
    confirmCurrentBatch: confirmCurrentBatchImpl,
    cancelCurrentBatch: cancelCurrentBatchImpl,
    isConfirmCommand: checkIsConfirmCommand,
    isCancelCommand: checkIsCancelCommand,
    fetchMessages: fetchMessagesImpl,
    refreshPendingActions: refreshPendingActionsImpl,
    handoff,
    setHandoff,
    loadingHandoff,
  }
}

// ── Internal helpers ───────────────────────────────────────────────────

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function parseContextStatus(data: Record<string, unknown> | null): ChatContextStatus | null {
  if (!data) return null
  const conversationId = finiteNumber(data.conversation_id)
  const contextWindowTokens = finiteNumber(data.context_window_tokens)
  const estimatedTokens = finiteNumber(data.estimated_tokens)
  const usedPercent = finiteNumber(data.used_percent)
  const thresholdPercent = finiteNumber(data.compression_threshold_percent)
  const thresholdTokens = finiteNumber(data.compression_threshold_tokens)
  const remainingTokens = finiteNumber(data.remaining_tokens)
  if (
    conversationId === null || contextWindowTokens === null || estimatedTokens === null ||
    usedPercent === null || thresholdPercent === null || thresholdTokens === null ||
    remainingTokens === null
  ) return null
  const progressPercent = finiteNumber(data.compression_progress_percent)
    ?? Math.min(100, Math.round((estimatedTokens / thresholdTokens) * 1000) / 10)
  const state = data.state === "compressing" || data.state === "compression_failed"
    ? data.state
    : "ready"
  return {
    conversation_id: conversationId,
    context_window_tokens: contextWindowTokens,
    estimated_tokens: estimatedTokens,
    used_percent: usedPercent,
    compression_progress_percent: progressPercent,
    compression_threshold_percent: thresholdPercent,
    compression_threshold_tokens: thresholdTokens,
    remaining_tokens: remainingTokens,
    summary_tokens: finiteNumber(data.summary_tokens) ?? 0,
    recent_message_count: finiteNumber(data.recent_message_count) ?? 0,
    compacted_through_message_id: finiteNumber(data.compacted_through_message_id),
    last_compacted_at: typeof data.last_compacted_at === "string" ? data.last_compacted_at : null,
    token_source: typeof data.token_source === "string" ? data.token_source : "estimate",
    state,
  }
}

function parseCompressionNotice(data: Record<string, unknown> | null): ContextCompressionNotice | null {
  if (!data || data.mode !== "persistent") return null
  const required = [
    "revision",
    "summarized_message_count",
    "summarized_turn_count",
    "duplicate_messages_omitted",
    "before_tokens",
    "after_tokens",
    "before_percent",
    "after_percent",
    "summary_tokens",
  ] as const
  if (required.some((key) => finiteNumber(data[key]) === null)) return null
  return {
    mode: "persistent",
    revision: data.revision as number,
    summarized_message_count: data.summarized_message_count as number,
    summarized_turn_count: data.summarized_turn_count as number,
    duplicate_messages_omitted: data.duplicate_messages_omitted as number,
    before_tokens: data.before_tokens as number,
    after_tokens: data.after_tokens as number,
    before_percent: data.before_percent as number,
    after_percent: data.after_percent as number,
    summary_tokens: data.summary_tokens as number,
  }
}

function extractApiErrorStatus(error: unknown): number | null {
  if (!error || typeof error !== "object") return null
  const err = error as Record<string, unknown>
  const response = err.response
  if (!response || typeof response !== "object") return null
  const status = (response as Record<string, unknown>).status
  return typeof status === "number" ? status : null
}

function extractApiErrorDetail(error: unknown): string {
  if (!error || typeof error !== "object") return ""
  const err = error as Record<string, unknown>
  const response = err.response
  if (!response || typeof response !== "object") return ""
  const data = (response as Record<string, unknown>).data
  if (typeof data === "string") return data
  if (!data || typeof data !== "object") return ""
  const detail = (data as Record<string, unknown>).detail
  return typeof detail === "string" ? detail : ""
}

function isConversationMissingError(error: unknown, detail = extractApiErrorDetail(error)): boolean {
  return extractApiErrorStatus(error) === 404 && detail.includes("Conversation not found")
}

function isUnknownActiveSkillsError(error: unknown): boolean {
  return extractApiErrorDetail(error).includes("Unknown active skills")
}
