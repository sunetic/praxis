import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { ChevronDown, Database, Loader2, MessageSquarePlus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import { ChatThreadView } from "@/components/chat/ChatThreadView"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { useChatController } from "@/components/chat/useChatController"
import { chatApi, conversationsApi, datasourcesApi, filterConnectableDatasources, skillsApi } from "@/lib/api"
import type { ChatHandoff, Conversation, DataSource, Skill } from "@/lib/api"
import { useShellI18n } from "@/i18n/shellI18n"

type AgentRunContext = {
  conversationId: number
  agentId?: number
  agentName?: string
  datasourceIds: number[]
  autoRun: boolean
}

type HandoffRouteContext = {
  conversationId: number
  handoffId: number
}

function parseNumericId(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function parseNumericIdList(value: unknown): number[] {
  if (typeof value !== "string" || !value.trim()) return []
  const normalized: number[] = []
  const seen = new Set<number>()
  for (const item of value.split(",")) {
    const parsed = parseNumericId(item.trim())
    if (parsed === null || parsed <= 0 || seen.has(parsed)) continue
    seen.add(parsed)
    normalized.push(parsed)
  }
  return normalized
}

function parseHandoffRoute(searchParams: URLSearchParams): HandoffRouteContext | null {
  const conversationId = parseNumericId(searchParams.get("conversationId"))
  const handoffId = parseNumericId(searchParams.get("handoffId"))
  if (conversationId === null || conversationId <= 0 || handoffId === null || handoffId <= 0) return null
  return { conversationId, handoffId }
}

export function ChatPage() {
  const { t } = useShellI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  const suggestions = useMemo(() => [
    { label: t("chat.suggestion.slowSql.label"), prompt: t("chat.suggestion.slowSql.prompt") },
    { label: t("chat.suggestion.conn.label"), prompt: t("chat.suggestion.conn.prompt") },
    { label: t("chat.suggestion.schema.label"), prompt: t("chat.suggestion.schema.prompt") },
    { label: t("chat.suggestion.health.label"), prompt: t("chat.suggestion.health.prompt") },
  ], [t])

  // ── Page-level state (conversation list, datasource, routing) ──
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [sceneConversations, setSceneConversations] = useState<Conversation[]>([])
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [currentConversation, setCurrentConversation] = useState<Conversation | null>(null)
  const [loading, setLoading] = useState(false)
  const [showDatasourceSelect, setShowDatasourceSelect] = useState(false)
  const [updatingDatasource, setUpdatingDatasource] = useState(false)
  const [clearingAll, setClearingAll] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const [agentRunContext, setAgentRunContext] = useState<AgentRunContext | null>(null)
  const [handoff, setHandoff] = useState<ChatHandoff | null>(null)
  const [, setLoadingHandoff] = useState(false)

  const datasourceMenuRef = useRef<HTMLDivElement>(null)
  const runContextAppliedRef = useRef<number | null>(null)
  const autoRunTriggeredRef = useRef<number | null>(null)

  // ── Unified chat controller ──
  const controller = useChatController({
    title: "Chat",
    datasourceId: currentConversation?.datasource_id ?? null,
    activeSkills: currentConversation?.active_skills,
    datasources,
    managedConversation: currentConversation,
    fetchOnConversationChange: true,
    onConversationCreated: (created) => {
      setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)])
      setCurrentConversation(created)
    },
    onSkillsChanged: (nextSkills) => {
      if (!currentConversation) return
      updateConversationState(currentConversation.id, { active_skills: nextSkills })
    },
    onDatasourceContextChanged: (nextDatasourceId) => {
      if (!currentConversation) return
      updateConversationState(currentConversation.id, { datasource_id: nextDatasourceId })
    },
  })

  // ── Page helpers ──

  const updateConversationState = (conversationId: number, patch: Partial<Conversation>) => {
    setConversations((prev) =>
      prev.map((item) => (item.id !== conversationId ? item : { ...item, ...patch }))
    )
    setCurrentConversation((prev) => {
      if (!prev || prev.id !== conversationId) return prev
      return { ...prev, ...patch }
    })
  }

  const parseAgentRunContextFromParams = (): AgentRunContext | null => {
    const from = String(searchParams.get("from") || "").trim().toLowerCase()
    const conversationId = parseNumericId(searchParams.get("conversationId"))
    if (from !== "agent" || conversationId === null || conversationId <= 0) return null
    return {
      conversationId,
      agentId: parseNumericId(searchParams.get("agentId")) ?? undefined,
      agentName: String(searchParams.get("agentName") || "").trim() || undefined,
      datasourceIds: parseNumericIdList(searchParams.get("runDatasourceIds")),
      autoRun: String(searchParams.get("autoRun") || "").trim() === "1",
    }
  }

  const clearAutoRunFlagFromParams = () => {
    if (!searchParams.has("autoRun")) return
    const next = new URLSearchParams(searchParams)
    next.delete("autoRun")
    setSearchParams(next, { replace: true })
  }

  const clearHandoffRouteFromParams = () => {
    if (!searchParams.has("handoffId") && !searchParams.has("conversationId")) return
    const next = new URLSearchParams(searchParams)
    next.delete("handoffId")
    if (next.get("from") !== "agent") next.delete("conversationId")
    setSearchParams(next, { replace: true })
  }

  // ── Data fetching ──

  const refreshConversations = async () => {
    const [primaryData, agentRunData, sceneData] = await Promise.all([
      conversationsApi.list({ category: "primary" }),
      conversationsApi.list({ category: "agent_run" }),
      conversationsApi.list({ category: "scene" }),
    ])
    const userInitiatedData = [...primaryData, ...agentRunData].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    )
    setConversations(userInitiatedData)
    setSceneConversations(sceneData)
    const combined = [...userInitiatedData, ...sceneData]
    setCurrentConversation((prev) => {
      if (!prev) return userInitiatedData[0] ?? sceneData[0] ?? null
      return combined.find((item) => item.id === prev.id) ?? userInitiatedData[0] ?? sceneData[0] ?? null
    })
    return { combined, primaryData: userInitiatedData }
  }

  const fetchInitial = async () => {
    setLoading(true)
    try {
      const [{ combined: conversationData, primaryData }, dsData, skillData] = await Promise.all([
        refreshConversations(),
        datasourcesApi.list(),
        skillsApi.list(),
      ])
      const runContext = parseAgentRunContextFromParams()
      const handoffRoute = parseHandoffRoute(searchParams)
      if (runContext) {
        const matched = conversationData.find((item) => item.id === runContext.conversationId)
        if (matched) setCurrentConversation(matched)
      } else if (handoffRoute) {
        const matched = conversationData.find((item) => item.id === handoffRoute.conversationId)
        if (matched) setCurrentConversation(matched)
      } else if (primaryData.length === 0) {
        const created = await conversationsApi.create({ title: t("chat.defaultTitle") })
        setConversations([created])
        setCurrentConversation(created)
      }
      setAgentRunContext(runContext)
      setDatasources(filterConnectableDatasources(dsData))
      setSkills(skillData)
    } catch {
      toast.error(t("chat.toast.loadFailed"))
    } finally {
      setLoading(false)
    }
  }

  // ── Effects ──

  useEffect(() => { void fetchInitial() }, [])

  useEffect(() => {
    const runContext = parseAgentRunContextFromParams()
    setAgentRunContext(runContext)
    if (!runContext || conversations.length === 0) return
    if (runContextAppliedRef.current === runContext.conversationId) return
    const matched = conversations.find((item) => item.id === runContext.conversationId)
    if (matched) {
      setCurrentConversation(matched)
      runContextAppliedRef.current = runContext.conversationId
    }
  }, [searchParams, conversations])

  useEffect(() => {
    const handoffRoute = parseHandoffRoute(searchParams)
    if (!handoffRoute || conversations.length === 0) return
    const matched = conversations.find((item) => item.id === handoffRoute.conversationId)
    if (matched && currentConversation?.id !== matched.id) setCurrentConversation(matched)
  }, [searchParams, conversations, currentConversation?.id])

  useEffect(() => {
    if (!showDatasourceSelect) return
    const handleClickOutside = (event: MouseEvent) => {
      if (!datasourceMenuRef.current?.contains(event.target as Node)) setShowDatasourceSelect(false)
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [showDatasourceSelect])

  // Handoff loading
  useEffect(() => {
    const handoffRoute = parseHandoffRoute(searchParams)
    if (!handoffRoute || !currentConversation || currentConversation.id !== handoffRoute.conversationId) {
      setHandoff(null)
      return
    }
    let cancelled = false
    setLoadingHandoff(true)
    chatApi
      .getHandoff(handoffRoute.conversationId, handoffRoute.handoffId)
      .then((data) => {
        if (cancelled) return
        if (data.status !== "pending") {
          setHandoff(null)
          clearHandoffRouteFromParams()
          return
        }
        setHandoff(data)
      })
      .catch(() => { if (!cancelled) setHandoff(null) })
      .finally(() => { if (!cancelled) setLoadingHandoff(false) })
    return () => { cancelled = true }
  }, [searchParams, currentConversation?.id])

  useEffect(() => {
    controller.setHandoff(handoff)
  }, [controller, handoff])

  // Auto-run agent
  useEffect(() => {
    if (!agentRunContext?.autoRun || !agentRunContext.agentId) return
    if (!currentConversation || currentConversation.id !== agentRunContext.conversationId) return
    if (controller.streaming || controller.savingAgent) return
    if (autoRunTriggeredRef.current === currentConversation.id) return
    if (controller.messages.length > 0) return
    const displayName = agentRunContext.agentName || ""
    const runCommand = `/run agent #${agentRunContext.agentId}${displayName ? ` ${displayName}` : ""}`
    autoRunTriggeredRef.current = currentConversation.id
    setAgentRunContext((prev) => {
      if (!prev || prev.conversationId !== currentConversation.id) return prev
      return { ...prev, autoRun: false }
    })
    clearAutoRunFlagFromParams()
    void controller.sendMessage(runCommand, { runDatasourceIds: agentRunContext.datasourceIds })
  }, [agentRunContext, currentConversation, controller.streaming, controller.savingAgent, controller.messages.length])

  // Post-stream refresh conversations
  useEffect(() => {
    if (controller.streaming) return
    if (!currentConversation) return
    void refreshConversations()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller.streaming])

  // ── Derived state ──

  const isSceneConversation = currentConversation?.category === "scene"

  const currentDatasourceLabel = useMemo(() => {
    if (!currentConversation?.datasource_id) return t("chat.datasource.select")
    const matched = datasources.find((item) => item.id === currentConversation.datasource_id)
    if (!matched) return `Datasource ${currentConversation.datasource_id}`
    return `${matched.name} (${matched.tenant_role})`
  }, [currentConversation?.datasource_id, datasources])

  const skillDescriptionByName = useMemo(() => {
    return new Map(skills.map((item) => [item.name, item.description || t("chat.skills.noDesc")]))
  }, [skills])

  const isAgentRunConversation = Boolean(agentRunContext) && currentConversation?.id === agentRunContext?.conversationId

  const agentRunDatasourceOptions = useMemo(() => {
    if (!isAgentRunConversation) return datasources
    const ids = agentRunContext?.datasourceIds || []
    if (ids.length === 0) return datasources
    const idSet = new Set(ids)
    return datasources.filter((item) => idSet.has(item.id))
  }, [datasources, isAgentRunConversation, agentRunContext?.datasourceIds])

  // ── Conversation management ──

  const handleCreateConversation = async () => {
    if (controller.streaming || controller.savingAgent) return
    try {
      const created = await conversationsApi.create({ title: t("chat.defaultTitle") })
      clearHandoffRouteFromParams()
      setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)])
      setCurrentConversation(created)
      setAgentRunContext(null)
      setHandoff(null)
      setShowDatasourceSelect(false)
    } catch {
      toast.error(t("chat.toast.createFailed"))
    }
  }

  const handleClearAll = async () => {
    if (controller.streaming || controller.savingAgent || clearingAll || conversations.length === 0) return
    setClearingAll(true)
    try {
      await Promise.all([...conversations, ...sceneConversations].map((item) => conversationsApi.delete(item.id)))
      setConversations([])
      setSceneConversations([])
      setCurrentConversation(null)
      setAgentRunContext(null)
      setHandoff(null)
      setClearConfirmOpen(false)
      clearHandoffRouteFromParams()
    } catch {
      toast.error(t("chat.toast.clearFailed"))
    } finally {
      setClearingAll(false)
    }
  }

  const handleSelectDatasource = async (datasourceId: number) => {
    if (!currentConversation || updatingDatasource || controller.savingAgent || isSceneConversation) return
    if (
      isAgentRunConversation &&
      (agentRunContext?.datasourceIds?.length || 0) > 0 &&
      !(agentRunContext?.datasourceIds || []).includes(datasourceId)
    ) {
      toast.error(t("chat.datasource.notInScope"))
      return
    }
    if (currentConversation.datasource_id === datasourceId) {
      setShowDatasourceSelect(false)
      return
    }
    setUpdatingDatasource(true)
    try {
      const updated = await conversationsApi.update(currentConversation.id, { datasource_id: datasourceId })
      updateConversationState(currentConversation.id, updated)
      setShowDatasourceSelect(false)
    } catch {
      toast.error(t("chat.toast.switchDsFailed"))
    } finally {
      setUpdatingDatasource(false)
    }
  }

  const handleUseHandoffPrompt = (prompt: string) => {
    if (!prompt.trim() || controller.streaming || controller.savingAgent) return
    void controller.sendMessage(prompt)
  }

  // ── Render ──

  const primary = (
    <div className="grid h-[calc(100vh-4.5rem)] min-w-0 animate-in grid-cols-1 gap-3 fade-in slide-in-from-bottom-1 duration-500 min-[901px]:grid-cols-[260px_minmax(0,1fr)] min-[901px]:gap-4">
      {/* Conversation sidebar */}
      <Card className="hidden min-h-0 overflow-hidden min-[901px]:block">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">{t("chat.sidebar.title")}</CardTitle>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleCreateConversation}
                disabled={controller.streaming || controller.savingAgent}
                className="h-8"
              >
                <MessageSquarePlus className="mr-1 size-4" />
                {t("chat.sidebar.new")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setClearConfirmOpen(true)}
                disabled={controller.streaming || controller.savingAgent || clearingAll || (conversations.length === 0 && sceneConversations.length === 0)}
                className="h-8 text-muted-foreground hover:text-negative"
              >
                <Trash2 className="mr-1 size-4" />
                {t("chat.sidebar.clearAll")}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="min-h-0 overflow-y-auto space-y-1">
          {loading ? (
            <div className="text-sm text-muted-foreground flex items-center gap-2">
              <Loader2 className="size-4 animate-spin" />
              {t("chat.sidebar.loading")}
            </div>
          ) : conversations.length === 0 && sceneConversations.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
              {t("chat.sidebar.empty")}
            </div>
          ) : (
            <>
              {conversations.map((item) => (
                <Button
                  key={item.id}
                  type="button"
                  variant="ghost"
                  className={`h-auto w-full justify-start rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    currentConversation?.id === item.id
                      ? "bg-accent text-foreground"
                      : "hover:bg-muted text-muted-foreground hover:text-foreground"
                  }`}
                  onClick={() => {
                    clearHandoffRouteFromParams()
                    setCurrentConversation(item)
                  }}
                  disabled={controller.streaming || controller.savingAgent}
                >
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="shrink-0 text-[11px] text-muted-foreground">#{item.id}</span>
                    <p className="truncate text-sm">{item.title || `${t("chat.sidebar.fallbackTitle")} ${item.id}`}</p>
                  </div>
                </Button>
              ))}
              {sceneConversations.length > 0 ? (
                <div className="pt-3">
                  <p className="px-3 pb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {t("chat.sidebar.sceneHistory")}
                  </p>
                  <div className="space-y-1">
                    {sceneConversations.map((item) => (
                      <Button
                        key={item.id}
                        type="button"
                        variant="ghost"
                        className={`h-auto w-full justify-start rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                          currentConversation?.id === item.id
                            ? "bg-accent text-foreground"
                            : "hover:bg-muted text-muted-foreground hover:text-foreground"
                        }`}
                        onClick={() => {
                          clearHandoffRouteFromParams()
                          setCurrentConversation(item)
                        }}
                        disabled={controller.streaming || controller.savingAgent}
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <span className="shrink-0 text-[11px] text-muted-foreground">#{item.id}</span>
                            <p className="truncate text-sm">{item.title || `${t("chat.sidebar.fallbackTitle")} ${item.id}`}</p>
                          </div>
                          <p className="truncate pt-0.5 text-[11px] text-muted-foreground">{item.scene_key || "scene"}</p>
                        </div>
                      </Button>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      {/* Main chat area */}
      <div className="min-h-0 min-w-0 grid grid-rows-[auto_minmax(0,1fr)] gap-4">
        {/* Datasource + skills bar */}
        <Card className="min-w-0 overflow-visible">
          <CardContent className="space-y-3 py-3">
            <select
              aria-label={t("chat.sidebar.title")}
              className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 min-[901px]:hidden"
              value={currentConversation?.id ?? ""}
              disabled={controller.streaming || controller.savingAgent || conversations.length + sceneConversations.length === 0}
              onChange={(event) => {
                const conversationId = Number(event.target.value)
                const selected = [...conversations, ...sceneConversations].find((item) => item.id === conversationId)
                if (!selected) return
                clearHandoffRouteFromParams()
                setCurrentConversation(selected)
              }}
            >
              {[...conversations, ...sceneConversations].map((item) => (
                <option key={item.id} value={item.id}>
                  #{item.id} {item.title || `${t("chat.sidebar.fallbackTitle")} ${item.id}`}
                </option>
              ))}
            </select>
            <div className="flex flex-wrap items-center gap-3">
              <div ref={datasourceMenuRef} className="relative">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 max-w-[280px] justify-between gap-2"
                  onClick={() => setShowDatasourceSelect((prev) => !prev)}
                  disabled={!currentConversation || controller.streaming || controller.savingAgent || updatingDatasource || isSceneConversation}
                >
                  <span className="min-w-0 flex items-center gap-2">
                    <Database className="size-3.5 text-muted-foreground" />
                    <span className="truncate text-xs">{currentDatasourceLabel}</span>
                  </span>
                  {updatingDatasource ? (
                    <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
                  ) : (
                    <ChevronDown className="size-3.5 text-muted-foreground" />
                  )}
                </Button>
                {showDatasourceSelect ? (
                  <div className="absolute top-full left-0 z-20 mt-2 w-80 rounded-lg border border-border bg-card p-1 shadow-md">
                    {agentRunDatasourceOptions.length === 0 ? (
                      <div className="px-2 py-2 text-xs text-muted-foreground">{t("chat.datasource.empty")}</div>
                    ) : (
                      agentRunDatasourceOptions.map((item) => (
                        <Button
                          key={item.id}
                          type="button"
                          variant="ghost"
                          className={`h-auto w-full justify-start rounded-lg px-2 py-2 text-left transition-colors ${
                            currentConversation?.datasource_id === item.id ? "bg-accent" : "hover:bg-muted"
                          }`}
                          onClick={() => handleSelectDatasource(item.id)}
                        >
                          <div className="text-xs font-medium text-foreground">{item.name}</div>
                          <div className="text-[11px] text-muted-foreground">
                            {item.cluster_key} · {item.tenant_role} · {item.host}:{item.port}
                          </div>
                        </Button>
                      ))
                    )}
                  </div>
                ) : null}
              </div>

              <div className="h-4 w-px bg-border" />
              <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-border bg-muted/60 px-3 py-1.5">
                <span className="shrink-0 text-xs text-muted-foreground">{t("chat.skills.label")}</span>
                {controller.activeSkills.length > 0 ? (
                  <div className="flex min-w-0 items-center gap-1">
                    {controller.activeSkills.slice(0, 3).map((name) => (
                      <Badge key={name} variant="secondary" className="max-w-[220px] truncate text-[10px]" title={skillDescriptionByName.get(name) || t("chat.skills.noDesc")}>
                        {name}
                      </Badge>
                    ))}
                    {controller.activeSkills.length > 3 ? (
                      <Badge variant="outline" className="text-[10px]">
                        +{controller.activeSkills.length - 3}
                      </Badge>
                    ) : null}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">{t("chat.skills.none")}</span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        <ChatThreadView
          controller={controller}
          suggestions={suggestions}
          datasources={datasources}
          readOnly={isSceneConversation}
          readOnlyHint={t("chat.readOnlyHint")}
          enableSaveAsAgent={true}
          enableHandoff={true}
          enableBatchActions={true}
          handoff={handoff}
          onHandoffUsed={handleUseHandoffPrompt}
          onNavigate={navigate}
          showHeader={false}
        />
      </div>

      <ConfirmActionDialog
        open={clearConfirmOpen}
        onOpenChange={setClearConfirmOpen}
        title={t("chat.clearDialog.title")}
        description={t("chat.clearDialog.desc")}
        confirmText={t("chat.clearDialog.confirm")}
        confirmingText={t("chat.clearDialog.confirming")}
        confirming={clearingAll}
        confirmDisabled={controller.streaming || controller.savingAgent || (conversations.length === 0 && sceneConversations.length === 0)}
        onConfirm={handleClearAll}
      />
    </div>
  )

  return <WorkbenchPage className="min-w-0" primary={primary} />
}
