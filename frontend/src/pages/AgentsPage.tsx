import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { AlertTriangle, Bot, Check, Database, Loader2, Pencil, Play, RefreshCw, Search, Sparkles, Trash2, Wrench, Blocks } from "lucide-react"
import { toast } from "sonner"

import { useShellI18n, type ShellCopyKey, type ShellTranslatorFn } from "@/i18n/shellI18n"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { agentsApi, chatApi, datasourcesApi, filterConnectableDatasources, messagesApi, skillsApi } from "@/lib/api"
import type { Agent, DataSource, Skill } from "@/lib/api"

const PAGE_SIZE = 10

const RUN_DS_SELECTION_STORAGE_KEY = "agent-run-datasource-selection:v1"

type EditingAgent = Partial<Agent>

type GuidedField = "goal" | "workflow" | "constraints" | "tools" | "skills"

type GuidedQuestion = {
  field: GuidedField
  prompt: ShellCopyKey
}

type GuidedMessage = {
  id: string
  role: "assistant" | "user"
  content: string
}

const GUIDED_QUESTIONS: GuidedQuestion[] = [
  { field: "goal", prompt: "agents.guided.q.goal" },
  { field: "workflow", prompt: "agents.guided.q.workflow" },
  { field: "constraints", prompt: "agents.guided.q.constraints" },
  { field: "tools", prompt: "agents.guided.q.tools" },
  { field: "skills", prompt: "agents.guided.q.skills" },
]

function normalizeCsvItems(raw: string): string[] {
  return raw
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function normalizeRunDatasourceSelection(raw: unknown): Record<number, number[]> {
  if (!raw || typeof raw !== "object") return {}
  const result: Record<number, number[]> = {}
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const agentId = Number(key)
    if (!Number.isInteger(agentId) || agentId <= 0) continue
    if (!Array.isArray(value)) continue
    const ids: number[] = []
    for (const item of value) {
      const datasourceId = Number(item)
      if (!Number.isInteger(datasourceId) || datasourceId <= 0) continue
      if (ids.includes(datasourceId)) continue
      ids.push(datasourceId)
    }
    result[agentId] = ids
  }
  return result
}

function deriveAgentName(seed: string, fallbackName: string): string {
  const cleaned = seed.replace(/[。！？.!?]/g, " ").trim()
  const short = cleaned.length > 20 ? cleaned.slice(0, 20) : cleaned
  if (!short) return fallbackName
  return short.endsWith("Agent") ? short : `${short} Agent`
}

function buildPromptFromGuide(answers: Partial<Record<GuidedField, string>>, t: ShellTranslatorFn): string {
  const goal = (answers.goal || "").trim()
  const workflow = (answers.workflow || "").trim()
  const constraints = (answers.constraints || "").trim()
  return [
    t("agents.promptRole"),
    "",
    t("agents.promptGoalHeading"),
    goal || t("agents.promptGoalDefault"),
    "",
    t("agents.promptWorkflowHeading"),
    workflow || t("agents.promptWorkflowDefault"),
    "",
    t("agents.promptConstraintsHeading"),
    constraints || t("agents.promptConstraintsDefault"),
  ].join("\n")
}

function buildDraftFromConversation(messages: { role: string; content: string }[], t: ShellTranslatorFn): EditingAgent {
  const userMessages = messages
    .filter((msg) => msg.role === "user")
    .map((msg) => (msg.content || "").trim())
    .filter(Boolean)
  const assistantMessages = messages
    .filter((msg) => msg.role === "assistant")
    .map((msg) => (msg.content || "").trim())
    .filter(Boolean)

  const latestUser = userMessages[userMessages.length - 1] || t("agents.handoff.dbAnalysisTask")
  const examples = userMessages.slice(-3).map((item, index) => `${index + 1}. ${item}`)
  const latestAssistant = assistantMessages[assistantMessages.length - 1] || ""

  const promptLines = [
    t("agents.handoff.promptRole"),
    "",
    t("agents.handoff.intentSummary"),
    ...examples,
    "",
    t("agents.handoff.replyStyle"),
    t("agents.handoff.replyStyleContent"),
  ]

  if (latestAssistant) {
    promptLines.push("", t("agents.handoff.refAnswerStyle"), latestAssistant.slice(0, 600))
  }

  return {
    name: deriveAgentName(latestUser, t("agents.newAgentFallback")),
    description: `${t("agents.handoff.descPrefix")}${latestUser.slice(0, 64)}`,
    prompt: promptLines.join("\n"),
    tools: [],
    skills: [],
  }
}

export function AgentsPage() {
  const { t } = useShellI18n()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [agents, setAgents] = useState<Agent[]>([])
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(1)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<{ id: number; name: string } | null>(null)
  const [guideOpen, setGuideOpen] = useState(false)
  const [guideMessages, setGuideMessages] = useState<GuidedMessage[]>([])
  const [guideInput, setGuideInput] = useState("")
  const [guideStep, setGuideStep] = useState(0)
  const [guideAnswers, setGuideAnswers] = useState<Partial<Record<GuidedField, string>>>({})
  const [loadingHandoff, setLoadingHandoff] = useState(false)
  const [runningAgentId, setRunningAgentId] = useState<number | null>(null)
  const [runDatasourceIdsByAgent, setRunDatasourceIdsByAgent] = useState<Record<number, number[]>>({})
  const [runDatasourcePickerAgentId, setRunDatasourcePickerAgentId] = useState<number | null>(null)
  const [runDatasourceFilter, setRunDatasourceFilter] = useState("")
  const [formData, setFormData] = useState<EditingAgent>({
    name: "",
    description: "",
    prompt: "",
    tools: [],
    skills: [],
  })
  const handoffHandledRef = useRef(false)
  const editParamHandledRef = useRef(false)

  const skillNameLookup = useMemo(() => {
    const lookup = new Map<string, string>()
    skills.forEach((skill) => {
      lookup.set(skill.name.toLowerCase(), skill.name)
    })
    return lookup
  }, [skills])

  const runnableDatasources = useMemo(() => {
    return datasources.filter((item) => String(item.status || "").toLowerCase() === "active")
  }, [datasources])

  const runDatasourcePickerAgent = useMemo(
    () => agents.find((item) => item.id === runDatasourcePickerAgentId) || null,
    [agents, runDatasourcePickerAgentId]
  )

  const filteredRunnableDatasources = useMemo(() => {
    const keyword = runDatasourceFilter.trim().toLowerCase()
    if (!keyword) return runnableDatasources
    return runnableDatasources.filter((item) => {
      return (
        item.name.toLowerCase().includes(keyword) ||
        (item.cluster_key || "").toLowerCase().includes(keyword) ||
        item.tenant_role.toLowerCase().includes(keyword) ||
        String(item.id).includes(keyword)
      )
    })
  }, [runnableDatasources, runDatasourceFilter])

  /* ---------- filtered + paginated agents ---------- */

  const visibleAgents = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return agents
    return agents.filter((a) =>
      `${a.name} ${a.description || ""}`.toLowerCase().includes(q)
    )
  }, [agents, query])

  const pagedAgents = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return visibleAgents.slice(start, start + PAGE_SIZE)
  }, [page, visibleAgents])

  useEffect(() => {
    setPage(1)
  }, [query])

  /* ---------- datasource run selection helpers ---------- */

  const getSelectedRunDatasourceIds = (agentId: number): number[] => {
    const selected = runDatasourceIdsByAgent[agentId] || []
    if (selected.length === 0) return []
    const availableIds = new Set(runnableDatasources.map((item) => item.id))
    const normalized: number[] = []
    for (const item of selected) {
      if (!availableIds.has(item)) continue
      if (normalized.includes(item)) continue
      normalized.push(item)
    }
    return normalized
  }

  const isRunDatasourceSelected = (agentId: number, datasourceId: number): boolean => {
    return getSelectedRunDatasourceIds(agentId).includes(datasourceId)
  }

  const handleToggleRunDatasource = (agentId: number, datasourceId: number, checked: boolean) => {
    setRunDatasourceIdsByAgent((prev) => {
      const current = prev[agentId] || []
      const set = new Set(current)
      if (checked) {
        set.add(datasourceId)
      } else {
        set.delete(datasourceId)
      }
      return {
        ...prev,
        [agentId]: Array.from(set),
      }
    })
  }

  const handleSelectAllRunDatasources = (agentId: number) => {
    setRunDatasourceIdsByAgent((prev) => ({
      ...prev,
      [agentId]: runnableDatasources.map((item) => item.id),
    }))
  }

  const handleClearRunDatasources = (agentId: number) => {
    setRunDatasourceIdsByAgent((prev) => ({
      ...prev,
      [agentId]: [],
    }))
  }

  const openRunDatasourcePicker = (agent: Agent) => {
    setRunDatasourceFilter("")
    setRunDatasourcePickerAgentId(agent.id)
  }

  const closeRunDatasourcePicker = () => {
    setRunDatasourceFilter("")
    setRunDatasourcePickerAgentId(null)
  }

  const getRunDatasourceSummary = (agentId: number): string => {
    const selectedIds = getSelectedRunDatasourceIds(agentId)
    if (selectedIds.length === 0) {
      return t("agents.dsNoneSelected")
    }
    const labels = selectedIds
      .map((id) => runnableDatasources.find((item) => item.id === id))
      .filter((item): item is DataSource => Boolean(item))
      .map((item) => `${item.name}（${item.tenant_role}）`)
    if (labels.length === 0) {
      return t("agents.dsSelected").replace("{count}", String(selectedIds.length))
    }
    if (labels.length === 1) {
      return t("agents.dsSelectedOne").replace("{label}", labels[0])
    }
    const preview = labels.slice(0, 2).join("、")
    if (labels.length > 2) {
      return t("agents.dsSelectedManyMore").replace("{count}", String(labels.length)).replace("{preview}", preview)
    }
    return t("agents.dsSelectedMany").replace("{count}", String(labels.length)).replace("{preview}", preview)
  }

  /* ---------- run agent ---------- */

  const handleRunAgent = (agent: Agent) => {
    if (runningAgentId !== null) return
    openRunDatasourcePicker(agent)
  }

  const handleConfirmRunAgent = async (agent: Agent) => {
    const selectedDatasourceIds = getSelectedRunDatasourceIds(agent.id)
    closeRunDatasourcePicker()
    setRunningAgentId(agent.id)
    try {
      const result = await agentsApi.run(agent.id, {
        datasource_ids: selectedDatasourceIds,
        title: t("agents.runSessionTitle").replace("{name}", agent.name),
      })
      const params = new URLSearchParams({
        from: "agent",
        autoRun: "1",
        conversationId: String(result.conversation.id),
        agentId: String(agent.id),
        agentName: agent.name,
      })
      if (result.datasource_ids.length > 0) {
        params.set("runDatasourceIds", result.datasource_ids.join(","))
      }
      navigate(`/chat?${params.toString()}`)
    } catch (error) {
      console.error("Failed to run agent:", error)
      toast.error(t("agents.toast.runFailed"))
    } finally {
      setRunningAgentId(null)
    }
  }

  /* ---------- fetch ---------- */

  const fetchAgents = async () => {
    setLoading(true)
    setError(null)
    try {
      const [agentsData, dsData] = await Promise.all([
        agentsApi.list(),
        datasourcesApi.list(),
      ])
      setAgents(agentsData)
      setDatasources(filterConnectableDatasources(dsData))
      setSkills(await skillsApi.list())
    } catch (err) {
      console.error("Failed to fetch:", err)
      setError(t("agents.toast.loadFailed"))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAgents()
  }, [])

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(RUN_DS_SELECTION_STORAGE_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      setRunDatasourceIdsByAgent(normalizeRunDatasourceSelection(parsed))
    } catch {
      setRunDatasourceIdsByAgent({})
    }
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(
        RUN_DS_SELECTION_STORAGE_KEY,
        JSON.stringify(runDatasourceIdsByAgent)
      )
    } catch {
      // Ignore storage failures.
    }
  }, [runDatasourceIdsByAgent])

  /* ---------- handoff from chat ---------- */

  const clearHandoffParams = useCallback(() => {
    const next = new URLSearchParams(searchParams)
    next.delete("handoff")
    next.delete("conversationId")
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const clearEditAgentParam = useCallback(() => {
    const next = new URLSearchParams(searchParams)
    next.delete("editAgentId")
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const startGuidedBuilder = () => {
    setGuideOpen(true)
    setGuideStep(0)
    setGuideAnswers({})
    setGuideInput("")
    setGuideMessages([
      {
        id: `assistant-start-${Date.now()}`,
        role: "assistant",
        content:
          t("agents.guided.intro"),
      },
      {
        id: `assistant-q-0-${Date.now()}`,
        role: "assistant",
        content: t(GUIDED_QUESTIONS[0].prompt),
      },
    ])
  }

  useEffect(() => {
    if (handoffHandledRef.current) return
    const handoff = searchParams.get("handoff")
    if (handoff !== "chat-save-agent") return
    handoffHandledRef.current = true

    const run = async () => {
      const conversationIdRaw = searchParams.get("conversationId")
      const conversationId = Number(conversationIdRaw)
      if (!Number.isInteger(conversationId) || conversationId <= 0) {
        toast.message(t("agents.toast.handoffEnter"))
        startGuidedBuilder()
        clearHandoffParams()
        return
      }

      setLoadingHandoff(true)
      try {
        const messages = await messagesApi.list(conversationId)
        const draft = buildDraftFromConversation(messages, t)

        let suggestedSkills: string[] = []
        try {
          const events = await chatApi.listEvents(conversationId)
          for (let index = events.length - 1; index >= 0; index -= 1) {
            const event = events[index]
            if (event.event_type !== "skill_delta" || !event.payload) continue
            const payload = event.payload as Record<string, unknown>
            if (!Array.isArray(payload.active_skills)) continue
            suggestedSkills = payload.active_skills
              .filter((item): item is string => typeof item === "string")
              .map((item) => item.trim())
              .filter(Boolean)
            break
          }
        } catch {
          suggestedSkills = []
        }
        if (suggestedSkills.length > 0) {
          draft.skills = suggestedSkills
        }

        setEditingId(null)
        setFormData(draft)
        setShowForm(true)
        toast.success(t("agents.toast.handoffDraft"))
      } catch (error) {
        console.error("Failed to hydrate chat handoff:", error)
        toast.error(t("agents.toast.handoffReadFailed"))
        startGuidedBuilder()
      } finally {
        setLoadingHandoff(false)
        clearHandoffParams()
      }
    }

    run()
  }, [clearHandoffParams, searchParams])

  /* ---------- guided builder ---------- */

  const buildDraftFromGuide = (
    answers: Partial<Record<GuidedField, string>>,
    existing: EditingAgent
  ): EditingAgent => {
    const goal = (answers.goal || "").trim()
    const workflow = (answers.workflow || "").trim()
    const toolItems = normalizeCsvItems(answers.tools || "")
    const skillItems = normalizeCsvItems(answers.skills || "")
      .map((item) => skillNameLookup.get(item.toLowerCase()) || "")
      .filter(Boolean)

    return {
      ...existing,
      name: existing.name?.trim() || deriveAgentName(goal || workflow || t("agents.newAgentFallback"), t("agents.newAgentFallback")),
      description:
        existing.description?.trim() ||
        [goal, workflow].filter(Boolean).join("；").slice(0, 180) ||
        t("agents.guided.draftDescFallback"),
      prompt: existing.prompt?.trim() || buildPromptFromGuide(answers, t),
      tools: toolItems.length > 0 ? Array.from(new Set(toolItems)) : existing.tools || [],
      skills: skillItems.length > 0 ? Array.from(new Set(skillItems)) : existing.skills || [],
    }
  }

  const handleGuideSend = () => {
    const text = guideInput.trim()
    if (!text) return
    const now = Date.now()
    const question = GUIDED_QUESTIONS[guideStep]
    const nextAnswers = {
      ...guideAnswers,
      [question.field]: text,
    }

    setGuideMessages((prev) => [
      ...prev,
      { id: `user-${now}`, role: "user", content: text },
    ])
    setGuideAnswers(nextAnswers)
    setGuideInput("")

    const nextStep = guideStep + 1
    if (nextStep < GUIDED_QUESTIONS.length) {
      setGuideStep(nextStep)
      setGuideMessages((prev) => [
        ...prev,
        {
          id: `assistant-q-${nextStep}-${Date.now()}`,
          role: "assistant",
          content: t(GUIDED_QUESTIONS[nextStep].prompt),
        },
      ])
      return
    }

    const draft = buildDraftFromGuide(nextAnswers, formData)
    setFormData(draft)
    setGuideOpen(false)
    setShowForm(true)
    setGuideStep(0)
    setGuideAnswers({})
    const unknownSkillInput = normalizeCsvItems(nextAnswers.skills || "").filter(
      (item) => !skillNameLookup.get(item.toLowerCase())
    )
    if (unknownSkillInput.length > 0) {
      toast.message(t("agents.guided.skillNotFound").replace("{items}", unknownSkillInput.join("，")))
    } else {
      toast.success(t("agents.guided.draftReady"))
    }
  }

  /* ---------- CRUD ---------- */

  const handleOpenForm = (agent?: Agent) => {
    if (agent) {
      setEditingId(agent.id)
      setFormData({
        name: agent.name,
        description: agent.description || "",
        prompt: agent.prompt,
        tools: agent.tools || [],
        skills: agent.skills || [],
      })
    } else {
      setEditingId(null)
      setFormData({
        name: "",
        description: "",
        prompt: "",
        tools: [],
        skills: [],
      })
    }
    setShowForm(true)
  }

  useEffect(() => {
    const editAgentIdRaw = searchParams.get("editAgentId")
    if (!editAgentIdRaw) {
      editParamHandledRef.current = false
      return
    }
    if (loading || editParamHandledRef.current) return

    const editAgentId = Number(editAgentIdRaw)
    if (!Number.isInteger(editAgentId) || editAgentId <= 0) {
      editParamHandledRef.current = true
      clearEditAgentParam()
      return
    }

    const matchedAgent = agents.find((agent) => agent.id === editAgentId)
    if (!matchedAgent) return
    editParamHandledRef.current = true
    handleOpenForm(matchedAgent)
    clearEditAgentParam()
  }, [agents, clearEditAgentParam, loading, searchParams])

  const handleCloseForm = () => {
    setShowForm(false)
    setEditingId(null)
  }

  const handleSave = async () => {
    if (!formData.name || !formData.prompt) {
      toast.error(t("agents.toast.namePromptRequired"))
      return
    }

    setSaving(true)
    try {
      const payload = {
        name: formData.name,
        description: formData.description,
        prompt: formData.prompt,
        tools: formData.tools,
        skills: formData.skills,
      }
      if (editingId) {
        await agentsApi.update(editingId, payload)
      } else {
        await agentsApi.create(payload)
      }
      await fetchAgents()
      handleCloseForm()
      toast.success(editingId ? t("agents.toast.updated") : t("agents.toast.created"))
    } catch (error) {
      console.error("Failed to save agent:", error)
      toast.error(t("agents.toast.saveFailed"))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteConfirm) return
    setDeletingId(deleteConfirm.id)
    try {
      await agentsApi.delete(deleteConfirm.id)
      await fetchAgents()
      toast.success(t("agents.toast.deleted"))
      setDeleteConfirm(null)
    } catch (error) {
      console.error("Failed to delete agent:", error)
      toast.error(t("agents.toast.deleteFailed"))
    } finally {
      setDeletingId(null)
    }
  }

  /* ---------- toolbar ---------- */

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            placeholder={t("agents.searchPlaceholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-72 rounded-lg bg-card pl-9 text-sm"
          />
        </div>
        <Button variant="outline" size="sm" onClick={fetchAgents} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {t("agents.refresh")}
        </Button>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button size="sm" onClick={startGuidedBuilder} disabled={loadingHandoff}>
          <Sparkles className="size-4" />
          {t("agents.newAgent")}
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  /* ---------- primary table ---------- */

  const COL_COUNT = 6

  const primary = (
    <section className="animate-in fade-in slide-in-from-bottom-1 duration-500">
      {loadingHandoff ? (
        <div className="mb-4 rounded-lg border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
          {t("agents.handoffLoading")}
        </div>
      ) : null}

      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[220px]">{t("agents.col.name")}</TableHead>
              <TableHead>{t("agents.col.description")}</TableHead>
              <TableHead className="w-28">{t("agents.col.type")}</TableHead>
              <TableHead className="w-20">{t("agents.col.toolCount")}</TableHead>
              <TableHead className="w-20">{t("agents.col.datasource")}</TableHead>
              <TableHead className="w-28 text-right">{t("agents.col.actions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <ListTableLoadingRows rowCount={6} columnCount={COL_COUNT} />
            ) : error ? (
              <TableRow>
                <TableCell colSpan={COL_COUNT} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <AlertTriangle className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">{error}</p>
                    <Button variant="ghost" size="sm" onClick={() => fetchAgents()}>
                      {t("agents.retry")}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ) : pagedAgents.length === 0 ? (
              <TableRow>
                <TableCell colSpan={COL_COUNT} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <Bot className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">
                      {query ? t("agents.emptyNoMatch") : t("agents.emptyNone")}
                    </p>
                    {query ? (
                      <Button variant="ghost" size="sm" onClick={() => setQuery("")}>
                        {t("agents.clearSearch")}
                      </Button>
                    ) : (
                      <Button variant="ghost" size="sm" onClick={startGuidedBuilder}>
                        <Sparkles className="size-4" />
                        {t("agents.newAgent")}
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              pagedAgents.map((agent, index) => (
                <TableRow
                  key={agent.id}
                  style={{ animationDelay: `${index * 30}ms` }}
                  className="cursor-pointer transition-colors duration-150 hover:bg-muted/40"
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                        <Bot className="size-3.5 text-muted-foreground" />
                      </div>
                      <span className="font-medium text-foreground">{agent.name}</span>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[400px] truncate text-muted-foreground">
                    {agent.description || t("agents.noDescription")}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={agent.agent_type === "built_in" ? "secondary" : "outline"}
                      className="text-[11px]"
                    >
                      {agent.agent_type === "built_in" ? t("shared.term.builtIn") : t("shared.term.custom")}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular-nums">
                    {(agent.tools?.length || 0) + (agent.skills?.length || 0)}
                  </TableCell>
                  <TableCell>
                    <span className="truncate text-xs text-muted-foreground">
                      {getRunDatasourceSummary(agent.id).length > 16
                        ? getRunDatasourceSummary(agent.id).slice(0, 16) + "..."
                        : getRunDatasourceSummary(agent.id)}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button
                        variant="outline"
                        size="icon-xs"
                        disabled={runningAgentId !== null}
                        onClick={() => handleRunAgent(agent)}
                      >
                        {runningAgentId === agent.id ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Play className="size-3.5" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => handleOpenForm(agent)}
                      >
                        <Pencil className="size-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="text-destructive hover:text-destructive"
                        onClick={() => setDeleteConfirm({ id: agent.id, name: agent.name })}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
        {!loading && !error ? (
          <PaginationFooter
            page={page}
            pageSize={PAGE_SIZE}
            total={visibleAgents.length}
            onPageChange={setPage}
            className="border-t border-border px-4 py-2"
          />
        ) : null}
      </ListTable>
    </section>
  )

  /* ---------- render ---------- */

  return (
    <>
      <div className="space-y-6">
        {/* Filter card */}
        <div className="rounded-xl bg-card p-4 shadow-sm">
          {toolbar}
        </div>
        {/* Primary card */}
        <div className="rounded-xl bg-card shadow-sm">
          {primary}
        </div>
      </div>

      {/* Edit / Create Dialog */}
      <Dialog
        open={showForm}
        onOpenChange={(open) => {
          if (!open) {
            handleCloseForm()
          } else {
            setShowForm(true)
          }
        }}
      >
        <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? t("agents.dialogTitleEdit") : t("agents.dialogTitleCreate")}</DialogTitle>
            <DialogDescription>{t("agents.dialogDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">{t("agents.label.name")}</label>
              <Input
                value={formData.name || ""}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder={t("agents.placeholder.name")}
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">{t("agents.label.description")}</label>
              <Input
                value={formData.description || ""}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder={t("agents.placeholder.description")}
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">{t("agents.label.prompt")}</label>
              <Textarea
                value={formData.prompt || ""}
                onChange={(e) => setFormData({ ...formData, prompt: e.target.value })}
                placeholder={t("agents.placeholder.prompt")}
                className="h-32"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">{t("agents.label.skills")}</label>
              <div className="space-y-2 max-h-40 overflow-y-auto rounded-md border border-border p-3">
                {skills.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t("agents.noSkill")}</p>
                ) : (
                  skills.map((skill) => (
                    <label key={skill.name} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={(formData.skills || []).includes(skill.name)}
                        onChange={(e) => {
                          const names = formData.skills || []
                          if (e.target.checked) {
                            setFormData({ ...formData, skills: [...names, skill.name] })
                          } else {
                            setFormData({ ...formData, skills: names.filter((name) => name !== skill.name) })
                          }
                        }}
                      />
                      <span className="text-sm">{skill.name}</span>
                      <span className="text-xs text-muted-foreground">- {skill.description}</span>
                    </label>
                  ))
                )}
              </div>
            </div>

            <DialogFooter className="gap-2 pt-4">
              <Button variant="outline" onClick={handleCloseForm}>
                {t("agents.cancel")}
              </Button>
              <Button onClick={handleSave} disabled={saving}>
                {saving ? <Loader2 className="size-4 mr-2 animate-spin" /> : <Check className="size-4 mr-2" />} {t("agents.save")}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* Run Datasource Picker Dialog */}
      <Dialog open={runDatasourcePickerAgentId !== null} onOpenChange={(open) => !open && closeRunDatasourcePicker()}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {runDatasourcePickerAgent
                ? t("agents.runDsDialogTitle").replace("{name}", runDatasourcePickerAgent.name)
                : t("agents.runDsDialogTitleDefault")}
            </DialogTitle>
            <DialogDescription>{t("agents.runDsDialogDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {/* Capabilities section */}
            {runDatasourcePickerAgent ? (
              <div>
                <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {t("agents.runDsCapabilities")}
                </h4>
                {(runDatasourcePickerAgent.tools?.length || 0) + (runDatasourcePickerAgent.skills?.length || 0) > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {(runDatasourcePickerAgent.tools || []).map((tool) => (
                      <span key={`tool-${tool}`} className="inline-flex items-center gap-1 rounded-md bg-muted/60 px-2 py-1 text-xs text-muted-foreground">
                        <Wrench className="size-3" />
                        {tool}
                      </span>
                    ))}
                    {(runDatasourcePickerAgent.skills || []).map((skill) => (
                      <span key={`skill-${skill}`} className="inline-flex items-center gap-1 rounded-md bg-primary/[0.06] px-2 py-1 text-xs text-primary">
                        <Blocks className="size-3" />
                        {skill}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">{t("agents.runDsNoCapabilities")}</p>
                )}
              </div>
            ) : null}

            {/* Datasources section */}
            <div>
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {t("agents.runDsDatasources")}
                  {runDatasourcePickerAgent && getSelectedRunDatasourceIds(runDatasourcePickerAgent.id).length > 1 ? (
                    <span className="ml-1.5 normal-case tracking-normal font-normal">· {t("agents.runDsDatasourcesHint")}</span>
                  ) : null}
                </h4>
                {runDatasourcePickerAgent && runnableDatasources.length >= 4 ? (
                  <div className="flex items-center">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-auto px-1.5 py-0.5 text-xs text-muted-foreground"
                      onClick={() => handleSelectAllRunDatasources(runDatasourcePickerAgent.id)}
                    >
                      {t("agents.runDsSelectAll")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-auto px-1.5 py-0.5 text-xs text-muted-foreground"
                      onClick={() => handleClearRunDatasources(runDatasourcePickerAgent.id)}
                    >
                      {t("agents.runDsClear")}
                    </Button>
                  </div>
                ) : null}
              </div>

              {/* Search — only when ≥ 4 datasources */}
              {runnableDatasources.length >= 4 ? (
                <div className="relative mb-2">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
                  <Input
                    value={runDatasourceFilter}
                    onChange={(event) => setRunDatasourceFilter(event.target.value)}
                    placeholder={t("agents.runDsSearchPlaceholder")}
                    className="pl-9"
                  />
                </div>
              ) : null}

              {/* Datasource list */}
              <div className="max-h-52 overflow-y-auto rounded-lg border border-border">
                {filteredRunnableDatasources.length === 0 ? (
                  <div className="flex flex-col items-center gap-2 py-8">
                    <Database className="size-7 text-muted-foreground/30" />
                    <p className="text-xs text-muted-foreground">{t("agents.runDsEmpty")}</p>
                  </div>
                ) : runDatasourcePickerAgent ? (
                  filteredRunnableDatasources.map((item, index) => {
                    const selected = isRunDatasourceSelected(runDatasourcePickerAgent.id, item.id)
                    return (
                      <div
                        key={item.id}
                        className={`flex cursor-pointer items-center gap-3 px-4 py-2.5 transition-colors hover:bg-muted/40 ${
                          index > 0 ? "border-t border-border" : ""
                        } ${selected ? "bg-primary/[0.04]" : ""}`}
                        onClick={() =>
                          handleToggleRunDatasource(
                            runDatasourcePickerAgent.id,
                            item.id,
                            !selected
                          )
                        }
                      >
                        <Checkbox
                          checked={selected}
                          onCheckedChange={(checked) =>
                            handleToggleRunDatasource(
                              runDatasourcePickerAgent.id,
                              item.id,
                              !!checked
                            )
                          }
                          onClick={(e) => e.stopPropagation()}
                        />
                        <div className="min-w-0 flex-1">
                          <span className="text-sm text-foreground">
                            {item.name}
                            <span className="ml-1.5 text-xs text-muted-foreground">({item.tenant_role})</span>
                          </span>
                          <p className="mt-0.5 truncate text-xs text-muted-foreground">
                            {item.cluster_key} · {item.host}:{item.port}
                          </p>
                        </div>
                      </div>
                    )
                  })
                ) : null}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeRunDatasourcePicker}>
              {t("agents.runDsCancel")}
            </Button>
            <Button
              disabled={runningAgentId !== null}
              onClick={() => runDatasourcePickerAgent && handleConfirmRunAgent(runDatasourcePickerAgent)}
            >
              {runningAgentId !== null ? (
                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
              ) : (
                <Play className="mr-1.5 size-3.5" />
              )}
              {runDatasourcePickerAgent && getSelectedRunDatasourceIds(runDatasourcePickerAgent.id).length > 0
                ? t("agents.runDsRunWithCount").replace("{count}", String(getSelectedRunDatasourceIds(runDatasourcePickerAgent.id).length))
                : t("agents.runDsRun")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmActionDialog
        open={!!deleteConfirm}
        onOpenChange={(open) => {
          if (!open) setDeleteConfirm(null)
        }}
        title={t("agents.deleteTitle")}
        description={
          <>
            {t("agents.deleteDescPre")} <span className="font-semibold text-foreground">{deleteConfirm?.name}</span> {t("agents.deleteDescPost")}
          </>
        }
        confirmText={t("agents.deleteConfirm")}
        confirming={deletingId !== null}
        confirmDisabled={deleteConfirm === null}
        onConfirm={handleDelete}
      />

      {/* Guided Builder Dialog */}
      <Dialog open={guideOpen} onOpenChange={setGuideOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("agents.guidedDialogTitle")}</DialogTitle>
            <DialogDescription>{t("agents.guidedDialogDesc")}</DialogDescription>
          </DialogHeader>

          <div className="rounded-lg border bg-muted/30 p-3 h-80 overflow-y-auto space-y-3">
            {guideMessages.map((item) => (
              <div key={item.id} className={item.role === "assistant" ? "flex justify-start" : "flex justify-end"}>
                <div
                  className={
                    item.role === "assistant"
                      ? "max-w-[90%] rounded-lg bg-card px-3 py-2 text-sm border border-border"
                      : "max-w-[90%] rounded-lg bg-primary text-primary-foreground px-3 py-2 text-sm"
                  }
                >
                  {item.content}
                </div>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Input
              value={guideInput}
              onChange={(e) => setGuideInput(e.target.value)}
              placeholder={t("agents.guidedInputPlaceholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  handleGuideSend()
                }
              }}
            />
            <Button onClick={handleGuideSend}>{t("agents.guidedSend")}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
