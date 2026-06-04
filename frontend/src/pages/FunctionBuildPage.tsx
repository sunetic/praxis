import { useCallback, useEffect, useRef, useState } from "react"
import { isAxiosError } from "axios"
import { ChevronDown, Loader2, Minus, Pencil, Plus } from "lucide-react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { toast } from "sonner"

import { useShellI18n, type ShellTranslatorFn } from "@/i18n/shellI18n"
import { ChatThreadView } from "@/components/chat/ChatThreadView"
import { useChatController } from "@/components/chat/useChatController"
import { buildConversationContext, type BuildChatMessageLike } from "@/lib/buildChatRuntime"
import { consumeRuntimeSse } from "@/lib/runtimeStream"
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Drawer, DrawerBody, DrawerContent, DrawerHeader, DrawerTitle } from "@/components/ui/drawer"
import { Input } from "@/components/ui/input"
import { datasourcesApi, functionsApi, type DataSource, type Message, type SceneAgentPayload } from "@/lib/api"

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

type JsonObject = { [key: string]: JsonValue }

type InputRow = { id: string; key: string; value: string }
type FunctionRecord = {
  id: number
  name?: string
  slug?: string
  status?: string
  description?: string
  updated_at?: string
}
type FunctionBuildRunRecord = {
  run_id: string
  action?: string
  status?: string
  prompt?: string
  result_summary?: string
  error_summary?: string
}
type SuggestInputResponse = {
  payload?: JsonObject
  rationale?: string
  missing_information?: string[]
  assumptions?: string[]
}
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

type DatasourceOption = Pick<DataSource, "id" | "name" | "tenant_role" | "status">

function buildFunctionScenePayload(item: FunctionRecord | null): SceneAgentPayload | undefined {
  if (!item) return undefined
  const focusObject = {
    kind: "function",
    function_id: item.id,
    name: item.name || null,
    slug: item.slug || null,
    status: item.status || null,
  }
  return {
    key: "function_build",
    context: {
      page: "function-build",
      ...focusObject,
    },
    focus_object: focusObject,
  }
}

function rowsFromPayload(payload: JsonObject | null | undefined): InputRow[] {
  if (!payload || typeof payload !== "object") return [{ id: `row-${Date.now()}`, key: "", value: "" }]
  const entries = Object.entries(payload)
  if (entries.length === 0) return [{ id: `row-${Date.now()}`, key: "", value: "" }]
  return entries.map(([key, value], idx) => ({
    id: `row-${Date.now()}-${idx}`,
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }))
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

function toFriendlyInvokeError(t: ShellTranslatorFn, message: string, errorCode?: string): string {
  const raw = String(message || "").trim()
  const byCode: Record<string, string> = {
    release_required: t("fnBuild.error.releaseRequired"),
    datasource_required: t("fnBuild.error.datasourceRequired"),
    sql_param_placeholder: t("fnBuild.error.sqlParamPlaceholder"),
    sql_syntax_error: t("fnBuild.error.sqlSyntaxError"),
    sql_object_not_found: t("fnBuild.error.sqlObjectNotFound"),
  }
  const code = String(errorCode || "").trim()
  if (code && byCode[code]) return byCode[code]
  return raw || t("fnBuild.error.invokeFallback")
}

export function FunctionBuildPage() {
  const { t } = useShellI18n()
  const navigate = useNavigate()
  const { functionId } = useParams()
  const id = Number(functionId)

  const [item, setItem] = useState<FunctionRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [metaName, setMetaName] = useState("")
  const [metaDescription, setMetaDescription] = useState("")
  const [editingTitle, setEditingTitle] = useState(false)
  const [editingDescription, setEditingDescription] = useState(false)
  const [savingMeta, setSavingMeta] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [inputRows, setInputRows] = useState<InputRow[]>([{ id: "row-initial", key: "", value: "" }])
  const [suggestingInput, setSuggestingInput] = useState(false)
  const [invoking, setInvoking] = useState(false)
  const [invokeOutput, setInvokeOutput] = useState<JsonValue | null>(null)
  const [invokeError, setInvokeError] = useState("")
  const [invokeMeta, setInvokeMeta] = useState<{ status?: string; durationMs?: number; runId?: string } | null>(null)
  const [runDrawerOpen, setRunDrawerOpen] = useState(false)
  const [lastInvokePayload, setLastInvokePayload] = useState<JsonObject | null>(null)
  const [datasources, setDatasources] = useState<DatasourceOption[]>([])
  const [selectedDatasourceId, setSelectedDatasourceId] = useState("")
  const [datasourceMenuOpen, setDatasourceMenuOpen] = useState(false)
  const titleInputRef = useRef<HTMLInputElement | null>(null)
  const descriptionInputRef = useRef<HTMLInputElement | null>(null)
  const datasourceMenuRef = useRef<HTMLDivElement | null>(null)

  const handleStreamDone = useCallback((donePayload: Record<string, unknown>) => {
    const nextFunction = donePayload.function
    if (nextFunction && typeof nextFunction === "object") {
      setItem(nextFunction as FunctionRecord)
    }
  }, [])

  const chatController = useChatController({
    title: "Build Chat",
    datasourceId: null,
    sceneAgentPayload: buildFunctionScenePayload(item),
    sceneConversationMeta: item ? { sceneKey: "function_build" } : null,
    builderScope: item
      ? {
          scopeObjectType: "function",
          scopeObjectId: String(item.id),
        }
      : undefined,
    conversationContextBuilder: (messages, nextInput) => buildConversationContext(messages as BuildChatMessageLike[], nextInput, 10),
    onStreamDone: handleStreamDone,
  })

  useEffect(() => {
    if (!Number.isInteger(id)) {
      navigate("/function", { replace: true })
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.all([functionsApi.get(id), functionsApi.listBuildRuns(id, 30)])
      .then(([data, runs]) => {
        if (cancelled) return
        setItem(data)
        const orderedRuns = [...((runs || []) as FunctionBuildRunRecord[])].reverse()
        const restoredMessages: Message[] = []
        let seq = 0
        orderedRuns.forEach((run) => {
          const action = String(run.action || "").trim()
          const prompt = String(run.prompt || "").trim()
          const summary = String(run.result_summary || run.error_summary || "").trim()
          if (action === "build" && prompt) {
            seq += 1
            restoredMessages.push({ id: seq, conversation_id: 0, role: "user", content: prompt, created_at: "" })
          }
          if (summary) {
            seq += 1
            restoredMessages.push({
              id: seq,
              conversation_id: 0,
              role: run.status === "failed" ? "status" : "assistant",
              content: summary,
              agent_name: run.status === "failed" ? undefined : "FunctionBuilderAgent",
              created_at: "",
            })
          }
        })
        if (restoredMessages.length > 0) {
          chatController.setMessages(restoredMessages)
        } else {
          chatController.setMessages([{
            id: Date.now(),
            conversation_id: 0,
            role: "assistant",
            content: t("fnBuild.welcomeMessage"),
            agent_name: "FunctionBuilderAgent",
            created_at: new Date().toISOString(),
          }])
        }
      })
      .catch(() => {
        if (!cancelled) {
          toast.error(t("fnBuild.loadFailed"))
          navigate("/function", { replace: true })
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [id, navigate])

  useEffect(() => {
    let cancelled = false
    datasourcesApi
      .list()
      .then((rows) => {
        if (cancelled) return
        const active = (rows || []).filter(
          (item) => String(item?.status || "").trim().toLowerCase() === "active"
        )
        setDatasources(
          active.map((item) => ({
            id: item.id,
            name: item.name,
            tenant_role: item.tenant_role,
            status: item.status,
          }))
        )
      })
      .catch(() => {
        if (!cancelled) setDatasources([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    setMetaName(String(item?.name || ""))
    setMetaDescription(String(item?.description || ""))
    setEditingTitle(false)
    setEditingDescription(false)
  }, [item?.id, item?.name, item?.description])

  useEffect(() => {
    if (!editingTitle) return
    titleInputRef.current?.focus()
    titleInputRef.current?.select()
  }, [editingTitle])

  useEffect(() => {
    if (!editingDescription) return
    descriptionInputRef.current?.focus()
    descriptionInputRef.current?.select()
  }, [editingDescription])

  useEffect(() => {
    if (!datasourceMenuOpen) return
    const handlePointerDown = (event: MouseEvent) => {
      if (!datasourceMenuRef.current?.contains(event.target as Node)) {
        setDatasourceMenuOpen(false)
      }
    }
    window.addEventListener("mousedown", handlePointerDown)
    return () => window.removeEventListener("mousedown", handlePointerDown)
  }, [datasourceMenuOpen])

  const buildInvokePayload = () => {
    const payload = payloadFromRows(inputRows)
    const selectedNumeric = Number(selectedDatasourceId)
    const hasSelectedDatasource =
      selectedDatasourceId.trim().length > 0 && Number.isFinite(selectedNumeric)
    if (hasSelectedDatasource) {
      const dsId = Math.trunc(selectedNumeric)
      const currentDatasourceId = payload["datasource_id"]
      const currentDatasourceIdCamel = payload["datasourceId"]
      const currentDatasourceIds = payload["datasource_ids"]
      const currentDatasourceIdsCamel = payload["datasourceIds"]
      if (currentDatasourceId == null && currentDatasourceIdCamel == null) {
        payload["datasource_id"] = dsId
      }
      if (currentDatasourceIds == null && currentDatasourceIdsCamel == null) {
        payload["datasource_ids"] = [dsId]
      }
    }
    return {
      payload,
      hasSelectedDatasource,
      selectedDatasourceNumeric: selectedNumeric,
    }
  }

  const persistFunctionName = async ({
    nextName,
    successMessage,
  }: {
    nextName: string
    successMessage?: string
  }) => {
    if (!item || savingMeta) return false
    const normalizedName = nextName.trim()
    const currentName = String(item.name || "").trim()

    if (!normalizedName) {
      setMetaName(currentName)
      setEditingTitle(false)
      toast.error(t("fnBuild.nameEmpty"))
      return false
    }

    if (normalizedName === currentName) {
      setEditingTitle(false)
      return true
    }

    setSavingMeta(true)
    try {
      const next = await functionsApi.update(item.id, { name: normalizedName })
      setItem(next)
      setEditingTitle(false)
      if (successMessage) toast.success(successMessage)
      return true
    } catch (error) {
      const detail = isAxiosError(error)
        ? String((error.response?.data as { detail?: unknown })?.detail || "")
        : ""
      setMetaName(currentName)
      setEditingTitle(false)
      toast.error(detail || t("fnBuild.saveFailed"))
      return false
    } finally {
      setSavingMeta(false)
    }
  }

  const persistFunctionDescription = async ({
    nextDescription,
    successMessage,
  }: {
    nextDescription: string
    successMessage?: string
  }) => {
    if (!item || savingMeta) return false
    const normalizedDescription = nextDescription.trim()
    const currentDescription = String(item.description || "").trim()

    if (normalizedDescription === currentDescription) {
      setEditingDescription(false)
      return true
    }

    setSavingMeta(true)
    try {
      const next = await functionsApi.update(item.id, {
        description: normalizedDescription || null,
      })
      setItem(next)
      setEditingDescription(false)
      if (successMessage) toast.success(successMessage)
      return true
    } catch (error) {
      const detail = isAxiosError(error)
        ? String((error.response?.data as { detail?: unknown })?.detail || "")
        : ""
      setMetaDescription(currentDescription)
      setEditingDescription(false)
      toast.error(detail || t("fnBuild.saveFailed"))
      return false
    } finally {
      setSavingMeta(false)
    }
  }

  const handleTitleCommit = async () => {
    await persistFunctionName({
      nextName: metaName,
      successMessage: t("fnBuild.nameUpdated"),
    })
  }

  const handleDescriptionCommit = async () => {
    await persistFunctionDescription({
      nextDescription: metaDescription,
      successMessage: t("fnBuild.descriptionUpdated"),
    })
  }

  const handleTitleCancel = () => {
    setMetaName(String(item?.name || ""))
    setEditingTitle(false)
  }

  const handleDescriptionCancel = () => {
    setMetaDescription(String(item?.description || ""))
    setEditingDescription(false)
  }

  const handleSaveDraft = async () => {
    if (!item || savingMeta || publishing) return false
    const nextName = metaName.trim()
    const nextDescription = metaDescription.trim()
    const currentName = String(item.name || "").trim()
    const currentDescription = String(item.description || "").trim()

    if (!nextName) {
      toast.error(t("fnBuild.nameEmpty"))
      return false
    }

    const payload: Record<string, unknown> = {}
    if (nextName !== currentName) payload.name = nextName
    if (nextDescription !== currentDescription) payload.description = nextDescription || null

    if (Object.keys(payload).length === 0) {
      setEditingTitle(false)
      setEditingDescription(false)
      toast.success(t("fnBuild.draftSaved"))
      return true
    }

    setSavingMeta(true)
    try {
      const next = await functionsApi.update(item.id, payload)
      setItem(next)
      setEditingTitle(false)
      setEditingDescription(false)
      toast.success(t("fnBuild.draftSaved"))
      return true
    } catch (error) {
      const detail = isAxiosError(error)
        ? String((error.response?.data as { detail?: unknown })?.detail || "")
        : ""
      setMetaName(currentName)
      setMetaDescription(currentDescription)
      setEditingTitle(false)
      setEditingDescription(false)
      toast.error(detail || t("fnBuild.saveFailed"))
      return false
    } finally {
      setSavingMeta(false)
    }
  }

  const handleRelease = async () => {
    if (!item || publishing || savingMeta) return
    const saved = await handleSaveDraft()
    if (!saved || !item?.id) return
    setPublishing(true)
    try {
      const released = await functionsApi.release(item.id, {})
      const nextFunction = released?.function || item
      setItem(nextFunction)
      toast.success(t("fnBuild.published"))
    } catch (error) {
      const detailPayload = isAxiosError(error)
        ? ((error.response?.data as { detail?: unknown })?.detail ?? "")
        : ""
      const detail =
        typeof detailPayload === "string"
          ? detailPayload
          : (typeof detailPayload === "object" && detailPayload
            ? String((detailPayload as { message?: unknown }).message || "")
            : "")
      toast.error(detail || t("fnBuild.publishFailed"))
    } finally {
      setPublishing(false)
    }
  }

  // readFunctionStreamDone — still needed for suggest_input and invoke (non-chat actions)
  const readFunctionStreamDone = async (response: Response) => {
    const result = await consumeRuntimeSse(response, {})
    const doneData = result.donePayload
    const assistantMessage = String(doneData.assistant_message || result.assistantText || "").trim()
    return { doneData, assistantMessage }
  }

  const handleSuggestInput = async () => {
    if (!item) return
    setSuggestingInput(true)
    try {
      const conversationContext = chatController.messages
        .filter((message) => message.role !== "status")
        .slice(-6)
        .map((message) => `${message.role}: ${message.content}`)
        .join("\n")
      const prompt = t("fnBuild.suggestInputPrompt")
      const response = await functionsApi.buildChatStream(item.id, {
        action: "suggest_input",
        prompt,
        conversation_context: conversationContext,
      })
      const { doneData, assistantMessage } = await readFunctionStreamDone(response)
      const suggestion = (doneData.suggestion || {}) as SuggestInputResponse
      setInputRows(rowsFromPayload(suggestion?.payload))
      const note = String(assistantMessage || suggestion?.rationale || t("fnBuild.suggestInputFallback"))
      chatController.appendMessage("assistant", note)
    } catch (error) {
      const detail = isAxiosError(error)
        ? String((error.response?.data as { detail?: unknown })?.detail || "")
        : ""
      toast.error(detail || t("fnBuild.suggestInputFailed"))
    } finally {
      setSuggestingInput(false)
    }
  }

  const handleRunTest = async () => {
    if (!item || invoking) return
    const { payload, hasSelectedDatasource, selectedDatasourceNumeric } = buildInvokePayload()
    setLastInvokePayload(payload)
    setRunDrawerOpen(true)
    setInvoking(true)
    setInvokeError("")
    setInvokeOutput(null)
    setInvokeMeta(null)
    try {
      const response = await functionsApi.buildChatStream(item.id, {
        action: "invoke",
        invoke: {
          payload,
          ...(hasSelectedDatasource ? { datasource_id: Math.trunc(selectedDatasourceNumeric) } : {}),
          write_mode: "readonly",
          execution_mode: "plan",
          runtime_path: "draft",
        },
      })
      const { doneData, assistantMessage } = await readFunctionStreamDone(response)
      const result = doneData as InvokeResponse
      const status = String(result?.status || "unknown")
      setInvokeMeta({
        status,
        durationMs: Number(result?.duration_ms || 0),
        runId: String(result?.run_id || ""),
      })
      if (status !== "success") {
        const detail = toFriendlyInvokeError(
          t,
          String(result?.error_message || t("fnBuild.testFailed")),
          String(result?.error_code || "")
        )
        setInvokeError(detail)
        setInvokeOutput(result?.output ?? null)
        chatController.appendMessage("assistant", `${t("fnBuild.testFailedPrefix")}${detail}`)
        return
      }
      setInvokeOutput(result?.output ?? null)
      chatController.appendMessage("assistant", String(assistantMessage || t("fnBuild.testSuccess")))
    } catch (error) {
      const detailPayload = isAxiosError(error)
        ? ((error.response?.data as { detail?: unknown })?.detail ?? "")
        : ""
      const rawDetail = typeof detailPayload === "string"
        ? detailPayload
        : (typeof detailPayload === "object" && detailPayload
          ? String((detailPayload as { message?: unknown }).message || "")
          : "")
      const fallbackDetail = String((error as Error)?.message || "")
      const rawCode = typeof detailPayload === "object" && detailPayload
        ? String((detailPayload as { error_code?: unknown }).error_code || "")
        : ""
      const detail = toFriendlyInvokeError(t, rawDetail || fallbackDetail || t("fnBuild.testFailed"), rawCode)
      setInvokeError(detail)
      chatController.appendMessage("assistant", `${t("fnBuild.testFailedPrefix")}${detail}`)
    } finally {
      setInvoking(false)
    }
  }

  const invokePreview = buildInvokePayload()
  const selectedDatasource =
    datasources.find((source) => String(source.id) === selectedDatasourceId) || null
  const datasourceButtonLabel = selectedDatasource
    ? `${selectedDatasource.name || `Datasource ${selectedDatasource.id}`} (#${selectedDatasource.id})`
    : t("fnBuild.datasourceNone")
  const drawerPayload = lastInvokePayload || invokePreview.payload

  return (
    <div className="flex h-[calc(100vh-4.5rem)] min-h-0 flex-col gap-3 bg-background p-2 md:p-3 animate-in fade-in duration-500">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink asChild>
              <Link to="/function">Functions</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{item?.name || `Function ${id}`}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="flex min-h-0 flex-col rounded-xl border border-border bg-card shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-500">
          <div className="shrink-0 border-b border-border px-4 py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1 grid gap-1">
              {editingTitle ? (
                <Input
                  ref={titleInputRef}
                  value={metaName}
                  onChange={(event) => setMetaName(event.target.value)}
                  onBlur={handleTitleCommit}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault()
                      event.currentTarget.blur()
                    }
                    if (event.key === "Escape") {
                      event.preventDefault()
                      handleTitleCancel()
                    }
                  }}
                  placeholder={t("fnBuild.namePlaceholder")}
                  disabled={loading || savingMeta}
                  aria-label={t("fnBuild.nameAria")}
                  className="h-10 border-transparent bg-transparent px-0 text-xl font-semibold shadow-none focus-visible:border-transparent focus-visible:ring-0"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setEditingTitle(true)}
                  disabled={loading}
                  className="group flex h-10 min-w-0 max-w-full items-center gap-2 text-left text-xl font-semibold tracking-tight text-foreground outline-none transition hover:text-primary"
                >
                  <span className="truncate">{item?.name || `Function ${id}`}</span>
                  <Pencil className="size-3.5 shrink-0 text-muted-foreground/0 transition group-hover:text-muted-foreground" />
                </button>
              )}

              {editingDescription ? (
                <Input
                  ref={descriptionInputRef}
                  value={metaDescription}
                  onChange={(event) => setMetaDescription(event.target.value)}
                  onBlur={() => {
                    void handleDescriptionCommit()
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault()
                      event.currentTarget.blur()
                    }
                    if (event.key === "Escape") {
                      event.preventDefault()
                      handleDescriptionCancel()
                    }
                  }}
                  placeholder={t("fnBuild.addDescription")}
                  disabled={loading || savingMeta}
                  aria-label={t("fnBuild.descriptionAria")}
                  className="h-8 border-transparent bg-transparent px-0 text-sm text-muted-foreground shadow-none focus-visible:border-transparent focus-visible:ring-0"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setEditingDescription(true)}
                  disabled={loading}
                  className="group flex h-8 min-w-0 items-center gap-2 text-left text-sm text-muted-foreground outline-none transition hover:text-foreground"
                >
                  <span className="truncate">{metaDescription.trim() || t("fnBuild.addDescription")}</span>
                  <Pencil className="size-3 shrink-0 text-muted-foreground/0 transition group-hover:text-muted-foreground" />
                </button>
              )}
              </div>

              <div className="flex shrink-0 items-center gap-2">
                <Button
                  variant="outline"
                  onClick={handleSaveDraft}
                  disabled={loading || savingMeta || publishing}
                >
                  {savingMeta ? <Loader2 className="size-4 animate-spin" /> : null}
                  {t("fnBuild.saveDraft")}
                </Button>
                <Button
                  onClick={handleRelease}
                  disabled={loading || savingMeta || publishing || String(item?.status || "").toLowerCase() === "released"}
                >
                  {publishing ? <Loader2 className="size-4 animate-spin" /> : null}
                  {String(item?.status || "").toLowerCase() === "released" ? t("fnBuild.statusReleased") : t("fnBuild.publish")}
                </Button>
              </div>
            </div>
          </div>

          <div className="flex min-h-0 flex-1 flex-col p-3">
            <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-border bg-card px-4 py-4">
              <div className="shrink-0 space-y-5">
                <div className="space-y-2">
                  <p className="text-sm font-semibold text-foreground">{t("fnBuild.datasource")}</p>
                  <div ref={datasourceMenuRef} className="relative">
                    <button
                      type="button"
                      aria-label={t("fnBuild.datasourceAria")}
                      aria-haspopup="listbox"
                      aria-expanded={datasourceMenuOpen}
                      onClick={() => setDatasourceMenuOpen((open) => !open)}
                      className="flex h-11 w-full items-center justify-between rounded-xl border border-border bg-card px-3 text-left text-sm text-foreground shadow-sm transition hover:border-border"
                    >
                      <span className="min-w-0 truncate">{datasourceButtonLabel}</span>
                      <ChevronDown
                        className={`size-4 shrink-0 text-muted-foreground transition-transform ${
                          datasourceMenuOpen ? "rotate-180" : ""
                        }`}
                      />
                    </button>
                    <div
                      className={`absolute left-0 top-[calc(100%+0.5rem)] z-20 w-full rounded-xl border border-border bg-card p-2 shadow-lg transition ${
                        datasourceMenuOpen ? "pointer-events-auto translate-y-0 opacity-100" : "pointer-events-none -translate-y-1 opacity-0"
                      }`}
                      role="listbox"
                    >
                      <button
                        type="button"
                        role="option"
                        aria-selected={!selectedDatasourceId}
                        onClick={() => {
                          setSelectedDatasourceId("")
                          setDatasourceMenuOpen(false)
                        }}
                        className={`flex w-full items-center rounded-xl px-3 py-2 text-left text-sm transition ${
                          !selectedDatasourceId ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted"
                        }`}
                      >
                        {t("fnBuild.datasourceNone")}
                      </button>
                      {datasources.map((source) => {
                        const active = String(source.id) === selectedDatasourceId
                        return (
                          <button
                            key={source.id}
                            type="button"
                            role="option"
                            aria-selected={active}
                            onClick={() => {
                              setSelectedDatasourceId(String(source.id))
                              setDatasourceMenuOpen(false)
                            }}
                            className={`mt-1 flex w-full flex-col rounded-xl px-3 py-2 text-left transition ${
                              active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted"
                            }`}
                          >
                            <span className="truncate text-sm font-medium">
                              {source.name || `Datasource ${source.id}`}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              #{source.id} · {source.tenant_role || "user"}
                            </span>
                          </button>
                        )
                      })}
                      {datasources.length === 0 ? (
                        <div className="px-3 py-2 text-sm text-muted-foreground">{t("fnBuild.datasourceEmpty")}</div>
                      ) : null}
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-foreground">{t("fnBuild.testParams")}</p>
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={handleSuggestInput} disabled={suggestingInput || loading}>
                      {suggestingInput ? <Loader2 className="size-4 animate-spin" /> : null}
                      {t("fnBuild.suggestInput")}
                    </Button>
                    <button
                      type="button"
                      aria-label={t("fnBuild.addParamAria")}
                      onClick={() => setInputRows((prev) => [...prev, { id: `row-${Date.now()}`, key: "", value: "" }])}
                      className="inline-flex size-9 items-center justify-center rounded-full border border-border bg-card text-muted-foreground transition hover:border-border hover:text-foreground"
                    >
                      <Plus className="size-4" />
                    </button>
                  </div>
                </div>
              </div>

              <div className="mt-4 min-h-0 flex-1 overflow-auto pr-1">
                <div className="grid grid-cols-[160px_minmax(0,1fr)_40px] gap-2 px-1 pb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                  <span>{t("fnBuild.paramName")}</span>
                  <span>{t("fnBuild.paramValue")}</span>
                  <span className="sr-only">{t("fnBuild.paramActionsAria")}</span>
                </div>
                <div className="space-y-2">
                  {inputRows.map((row, index) => (
                    <div key={row.id} className="grid grid-cols-[160px_minmax(0,1fr)_40px] items-center gap-2">
                      <Input
                        value={row.key}
                        placeholder={t("fnBuild.paramKeyPlaceholder")}
                        onChange={(event) =>
                          setInputRows((prev) =>
                            prev.map((item) =>
                              item.id === row.id ? { ...item, key: event.target.value } : item
                            )
                          )
                        }
                      />
                      <Input
                        value={row.value}
                        placeholder={t("fnBuild.paramValuePlaceholder")}
                        onChange={(event) =>
                          setInputRows((prev) =>
                            prev.map((item) =>
                              item.id === row.id ? { ...item, value: event.target.value } : item
                            )
                          )
                        }
                      />
                      <button
                        type="button"
                        aria-label={`${t("fnBuild.deleteParamAria")} ${index + 1}`}
                        onClick={() =>
                          setInputRows((prev) => {
                            if (prev.length <= 1) return [{ id: row.id, key: "", value: "" }]
                            return prev.filter((item) => item.id !== row.id)
                          })
                        }
                        className="inline-flex size-8 items-center justify-center rounded-full border border-border bg-card text-muted-foreground transition hover:border-border hover:text-foreground"
                      >
                        <Minus className="size-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-4 shrink-0">
                <Button onClick={handleRunTest} disabled={invoking || loading} className="w-full">
                  {invoking ? <Loader2 className="size-4 animate-spin" /> : null}
                  {t("fnBuild.runTest")}
                </Button>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  {t("fnBuild.runTestHint")}
                </p>
              </div>
            </section>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col rounded-xl border border-border bg-card shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-500">
          <ChatThreadView
            controller={chatController}
            title="Build Chat"
            placeholder={t("fnBuild.chatPlaceholder")}
            embedded
            showHeader
            enableSaveAsAgent={false}
            enableHandoff={false}
            enableBatchActions={false}
            className="flex-1"
          />
        </aside>
      </div>

      <Drawer open={runDrawerOpen} onOpenChange={setRunDrawerOpen}>
        <DrawerContent className="flex max-w-[620px] flex-col bg-card text-foreground shadow-md">
          <DrawerHeader>
            <DrawerTitle>{t("fnBuild.resultTitle")}</DrawerTitle>
          </DrawerHeader>

          <DrawerBody className="space-y-4">
            <section className="overflow-hidden rounded-xl border border-border bg-muted/40">
              <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                {t("fnBuild.inputJson")}
              </div>
              <pre className="max-h-[320px] overflow-auto p-3 text-xs leading-6 text-foreground">
                {JSON.stringify(drawerPayload, null, 2)}
              </pre>
            </section>

            <section className="overflow-hidden rounded-xl border border-border bg-muted/40">
              <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                {t("fnBuild.outputLabel")}
              </div>
              <div className="p-3">
                {invoking ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    {t("fnBuild.executing")}
                  </div>
                ) : invokeError ? (
                  <pre className="whitespace-pre-wrap break-all text-xs leading-6 text-destructive">{invokeError}</pre>
                ) : invokeOutput !== null ? (
                  <pre className="max-h-[320px] overflow-auto text-xs leading-6 text-foreground">
                    {JSON.stringify(invokeOutput, null, 2)}
                  </pre>
                ) : (
                  <div className="text-xs text-muted-foreground">{t("fnBuild.outputPlaceholder")}</div>
                )}
              </div>
            </section>

            <section className="rounded-xl border border-border bg-muted/40 p-3 text-xs leading-6 text-muted-foreground">
              <p>{t("fnBuild.metaStatus")} {invokeMeta?.status || (invoking ? "running" : "-")}</p>
              <p>{t("fnBuild.metaDuration")} {invokeMeta?.durationMs ? `${invokeMeta.durationMs}ms` : "-"}</p>
              <p>Run ID: {invokeMeta?.runId || "-"}</p>
            </section>
          </DrawerBody>
        </DrawerContent>
      </Drawer>
    </div>
  )
}
