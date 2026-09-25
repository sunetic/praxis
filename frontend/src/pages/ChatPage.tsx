import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Database, Loader2, MessageSquarePlus, ShieldCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RunConversationView } from "@/components/chat/RunConversationView"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { agentRunsApi, type RunConversation, type RunScene } from "@/lib/agentRuns"
import { agentsApi, datasourcesApi, type Agent, type DataSource } from "@/lib/api"
import { useShellI18n } from "@/i18n/shellI18nContext"

export function ChatPage() {
  const { locale, t } = useShellI18n()
  const [params, setParams] = useSearchParams()
  const [conversations, setConversations] = useState<RunConversation[]>([])
  const [sources, setSources] = useState<DataSource[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [scene, setScene] = useState<RunScene>({})
  const [error, setError] = useState(false)
  const [creating, setCreating] = useState(false)
  const [savingScope, setSavingScope] = useState(false)
  const [scopeError, setScopeError] = useState(false)
  const [sourcesError, setSourcesError] = useState(false)
  const [scopeAgent, setScopeAgent] = useState<Agent | null>(null)
  const [agentError, setAgentError] = useState(false)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const initializing = useRef<Promise<[RunConversation[], { sources: DataSource[]; error: boolean }]> | null>(null)
  const createBusy = useRef(false)
  useEffect(() => {
    let alive = true
    initializing.current ??= Promise.all([
      agentRunsApi.conversations().then(async records => records.length || params.get("conversationId") ? records : [await agentRunsApi.createConversation(t("runtime.newConversation"), { datasource_ids: [] })]),
      datasourcesApi.list().then(sources => ({ sources, error: false })).catch(() => ({ sources: [], error: true })),
    ])
    void initializing.current.then(([records, datasources]) => {
      if (!alive) return
      setConversations(records); setSources(datasources.sources.filter(source => source.status === "active")); setSourcesError(datasources.error)
      const requested = params.get("conversationId")
      const current = requested ? records.find(record => record.id === requested) : records[0]
      if (!current) throw new Error("Requested conversation is unavailable")
      setSelected(current.id); setScene(current.scene)
    }).catch(() => { if (alive) setError(true) })
    return () => { alive = false }
    // Initialization is shared across StrictMode mounts to avoid duplicate creation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    let alive = true
    setScopeAgent(null); setAgentError(false)
    if (scene.agent_id) {
      void agentsApi.get(scene.agent_id).then(agent => { if (alive) setScopeAgent(agent) })
        .catch(() => { if (alive) setAgentError(true) })
    }
    return () => { alive = false }
  }, [scene.agent_id])
  useEffect(() => {
    if (!scene.auto_approval) return
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 30_000)
    return () => window.clearInterval(timer)
  }, [scene.auto_approval])
  const scopeLoading = Boolean(scene.agent_id && scopeAgent?.id !== scene.agent_id)
  const scopedSources = scene.agent_id ? sources.filter(source => scopeAgent?.id === scene.agent_id && scopeAgent?.datasource_ids.includes(source.id)) : sources
  const effectiveDatasourceIds = scene.datasource_ids ?? (scene.agent_id && scopeAgent?.id === scene.agent_id ? scopeAgent.datasource_ids : [])
  const invalidScope = Boolean(scene.agent_id && scopeAgent?.id === scene.agent_id && effectiveDatasourceIds.some(id => !scopeAgent.datasource_ids.includes(id)))
  const multipleSources = effectiveDatasourceIds.length > 1
  const autoApprovalActive = Boolean(
    scene.auto_approval
    && scene.auto_approval.expires_at > now
    && scene.auto_approval.agent_id === (scene.agent_id ?? null)
    && effectiveDatasourceIds.length === 1
    && scene.auto_approval.datasource_id === effectiveDatasourceIds[0],
  )
  const select = (conversation: RunConversation) => {
    setSelected(conversation.id); setScene(conversation.scene); setScopeError(false)
    setParams({ conversationId: conversation.id }, { replace: true })
  }
  const changeScope = async (value: string) => {
    if (!selected || savingScope) return
    setSavingScope(true); setScopeError(false)
    const next = { ...scene, datasource_ids: value === "none" ? [] : [Number(value)], auto_approval: null }
    try {
      const saved = await agentRunsApi.updateConversation(selected, next)
      setConversations(current => current.map(item => item.id === saved.id ? saved : item))
      setScene(saved.scene)
    } catch { setScopeError(true) }
    finally { setSavingScope(false) }
  }
  const toggleAutoApproval = async () => {
    if (!selected || savingScope || effectiveDatasourceIds.length !== 1) return
    if (!autoApprovalActive && !window.confirm(t("runtime.autoApprovalConfirm"))) return
    setSavingScope(true); setScopeError(false)
    const next: RunScene = {
      ...scene,
      auto_approval: autoApprovalActive ? null : {
        tool_name: "request_database_change",
        agent_id: scene.agent_id ?? null,
        datasource_id: effectiveDatasourceIds[0],
        expires_at: Date.now() / 1000 + 30 * 60,
      },
    }
    try {
      const saved = await agentRunsApi.updateConversation(selected, next)
      setConversations(current => current.map(item => item.id === saved.id ? saved : item))
      setScene(saved.scene); setNow(Date.now() / 1000)
    } catch { setScopeError(true) }
    finally { setSavingScope(false) }
  }
  const create = async () => {
    if (createBusy.current) return
    createBusy.current = true; setCreating(true)
    try {
      const conversation = await agentRunsApi.createConversation(t("runtime.newConversation"), { ...scene, auto_approval: null })
      setConversations(current => [conversation, ...current]); select(conversation); setError(false)
    } catch { setError(true) }
    finally { createBusy.current = false; setCreating(false) }
  }
  const conversationSelect = <select
    aria-label={t("runtime.conversation")}
    className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
    disabled={savingScope}
    value={selected ?? ""}
    onChange={event => { const value = conversations.find(conversation => conversation.id === event.target.value); if (value) select(value) }}
  >
    {!selected && <option value="">{t("runtime.loading")}</option>}
    {conversations.map(conversation => <option key={conversation.id} value={conversation.id}>{conversation.title} · {new Date(conversation.created_at * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}</option>)}
  </select>
  const primary = <div className="grid h-[calc(100dvh-4.5rem)] min-h-[34rem] min-w-0 animate-in grid-cols-1 gap-3 fade-in slide-in-from-bottom-1 duration-300 min-[901px]:grid-cols-[260px_minmax(0,1fr)] min-[901px]:gap-4">
    <Card className="hidden min-h-0 gap-0 overflow-hidden py-0 min-[901px]:flex min-[901px]:flex-col">
      <CardHeader className="shrink-0 px-5 py-5">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{t("chat.sidebar.title")}</CardTitle>
          <Button variant="outline" size="sm" className="h-8" onClick={() => void create()} disabled={creating || savingScope}>
            {creating ? <Loader2 className="size-4 animate-spin" /> : <MessageSquarePlus className="size-4" />}
            {t("chat.sidebar.new")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 space-y-1 overflow-y-auto px-4 pb-5">
        {conversations.length === 0 ? <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">{t("chat.sidebar.empty")}</div> : conversations.map(conversation => <Button
          key={conversation.id}
          type="button"
          variant="ghost"
          className={`h-auto w-full justify-start rounded-lg px-3 py-2.5 text-left transition-colors ${selected === conversation.id ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
          onClick={() => select(conversation)}
          disabled={savingScope}
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{conversation.title}</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">{new Date(conversation.created_at * 1000).toLocaleString(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</p>
          </div>
        </Button>)}
      </CardContent>
    </Card>

    <div className="grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)] gap-4">
      <Card className="min-w-0 gap-0 overflow-visible py-0">
        <CardContent className="space-y-3 px-4 py-3 sm:px-5">
          <div className="min-[901px]:hidden">{conversationSelect}</div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
              <Database className="size-3.5 shrink-0" />
              <span className="sr-only">{t("runtime.datasourceScope")}</span>
              <select
                aria-label={t("runtime.datasourceScope")}
                className="h-8 max-w-[19rem] rounded-lg border border-border bg-background px-3 text-xs text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                disabled={savingScope || !selected || scopeLoading}
                value={invalidScope ? "unavailable" : multipleSources ? "selection" : effectiveDatasourceIds.length === 1 ? String(effectiveDatasourceIds[0]) : "none"}
                onChange={event => void changeScope(event.target.value)}
              >
                <option value="none">{t("runtime.noDatasource")}</option>
                {invalidScope && <option value="unavailable" disabled>{t("agents.unavailableSelection")}</option>}
                {multipleSources && <option value="selection" disabled>{effectiveDatasourceIds.map(id => sources.find(source => source.id === id)?.name ?? `#${id}`).join(", ")}</option>}
                {scopedSources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}
              </select>
            </label>
            {scopeAgent && <><div className="h-4 w-px bg-border" /><p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{scopeAgent.name}</p></>}
            <Button
              type="button"
              variant={autoApprovalActive ? "secondary" : "ghost"}
              size="sm"
              className="h-8"
              disabled={savingScope || !selected || effectiveDatasourceIds.length !== 1 || invalidScope}
              title={effectiveDatasourceIds.length !== 1 ? t("runtime.autoApprovalNeedsOneDatasource") : t("runtime.autoApprovalScope")}
              onClick={() => void toggleAutoApproval()}
            >
              <ShieldCheck className="size-4" />
              {t(autoApprovalActive ? "runtime.autoApprovalOn" : "runtime.autoApprovalOff")}
            </Button>
            <Button variant="ghost" size="sm" className="h-8 min-[901px]:hidden" onClick={() => void create()} disabled={creating || savingScope}>
              <MessageSquarePlus className="size-4" />{t("runtime.newConversation")}
            </Button>
          </div>
          {savingScope && <p role="status" className="text-xs text-muted-foreground">{t("runtime.savingScopeBeforeSendingExistingRunsKeep")}</p>}
          {scopeError && <p role="alert" className="text-sm text-destructive">{t("runtime.scopeWasNotSavedThePreviousScope")}</p>}
          {invalidScope && <p role="alert" className="text-sm text-destructive">{t("agents.scopeRevoked")}</p>}
          {(sourcesError || agentError) && <p role="alert" className="text-sm text-destructive">{t("runtime.datasourceListIsUnavailableYouCanStill")}</p>}
          {error && <div role="alert" className="flex items-center gap-3 text-sm text-destructive"><p>{t("runtime.couldNotLoadOrCreateTheConversation")}</p><Button variant="outline" size="sm" onClick={() => window.location.reload()}>{t("runtime.reload")}</Button></div>}
        </CardContent>
      </Card>
      <RunConversationView conversationId={selected} scene={scene} disabled={savingScope || invalidScope} />
    </div>
  </div>
  return <WorkbenchPage className="min-w-0" primary={primary} />
}
