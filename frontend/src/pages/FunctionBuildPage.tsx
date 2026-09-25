import { useCallback, useEffect, useRef, useState } from "react"
import { isAxiosError } from "axios"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { RunConversationView } from "@/components/chat/RunConversationView"
import { Button } from "@/components/ui/button"
import { useShellI18n } from "@/i18n/shellI18nContext"
import { datasourcesApi, functionsApi, type DataSource } from "@/lib/api"
import { agentRunsApi, type RunConversation, type RunEvent, type RunView } from "@/lib/agentRuns"
import { canPublishDraft, functionArtifactsApi, type FunctionDraft } from "@/lib/functionArtifacts"

type FunctionRecord = { id: number; name: string; description?: string | null; kind?: string }
type Editor = { code: string; manifest: string; revision: string }
const fieldClass = "min-h-11 w-full rounded-md border border-input bg-background p-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"

export function FunctionBuildPage() {
  const { functionId } = useParams()
  return <FunctionWorkspace key={functionId} id={Number(functionId)} />
}

function FunctionWorkspace({ id }: { id: number }) {
  const { t, locale } = useShellI18n()
  const [params, setParams] = useSearchParams()
  const [item, setItem] = useState<FunctionRecord | null>(null)
  const [draft, setDraft] = useState<FunctionDraft | null>(null)
  const [conversations, setConversations] = useState<RunConversation[]>([])
  const [selected, setSelected] = useState<RunConversation | null>(null)
  const [sources, setSources] = useState<DataSource[]>([])
  const [sourcesError, setSourcesError] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const [stale, setStale] = useState(false)
  const [busy, setBusy] = useState(false)
  const [scopeBusy, setScopeBusy] = useState(false)
  const [scopeError, setScopeError] = useState(false)
  const [operationError, setOperationError] = useState("")
  const [notice, setNotice] = useState("")
  const [editor, setEditor] = useState<Editor | null>(null)
  const [payload, setPayload] = useState("{}")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const initialized = useRef<Promise<[FunctionRecord, FunctionDraft, RunConversation[]]> | null>(null)
  const refreshVersion = useRef(0)
  const operationBusy = useRef(false)
  const conversationBusy = useRef(false)
  const alive = useRef(true)
  const readonly = ["built_in", "builtin"].includes(item?.kind ?? "")

  useEffect(() => {
    alive.current = true
    let active = true
    if (!Number.isSafeInteger(id) || id < 1) { setLoadError(true); return }
    initialized.current ??= Promise.all([
      functionsApi.get(id), functionArtifactsApi.read(id),
      agentRunsApi.conversations().then(async records => {
        const matching = records.filter(record => record.scene.function_ids?.length === 1 && record.scene.function_ids[0] === id)
        // An explicit URL must resolve to this artifact; never silently switch scope.
        if (params.get("conversationId") && !matching.some(record => record.id === params.get("conversationId"))) throw new Error("Conversation scope mismatch")
        return matching.length ? matching : [await agentRunsApi.createConversation(t("artifact.workspace"), { function_ids: [id], datasource_ids: [] })]
      }),
    ])
    void initialized.current.then(([record, current, records]) => {
      if (!active) return
      setItem(record); setName(record.name); setDescription(record.description ?? ""); setDraft(current); setConversations(records)
      setSelected(records.find(value => value.id === params.get("conversationId")) ?? records[0])
    }).catch(() => { if (active) setLoadError(true) })
    void datasourcesApi.list().then(values => { if (active) setSources(values.filter(source => source.status === "active")) }).catch(() => { if (active) setSourcesError(true) })
    return () => { active = false; alive.current = false }
    // One initialization per artifact, shared across StrictMode effect replays.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current
    try {
      const current = await functionArtifactsApi.read(id)
      if (alive.current && version === refreshVersion.current) { setDraft(current); setStale(false) }
    } catch { if (alive.current && version === refreshVersion.current) setStale(true) }
  }, [id])
  const onEvent = useCallback((event: RunEvent, view: RunView) => {
    const call = event.type === "tool_result" ? view.blocks.find(block => block.kind === "tool" && block.id === event.call_id) : undefined
    const changed = call?.kind === "tool" && ["function_write", "function_edit", "function_validate", "function_publish"].includes(call.name)
    if (changed || event.type === "run_finished" || event.type === "run_cancelled" || event.type === "run_failed") void refresh()
  }, [refresh])
  const select = (conversation: RunConversation) => {
    setSelected(conversation); setScopeError(false)
    setParams({ conversationId: conversation.id }, { replace: true })
  }
  const createConversation = async () => {
    if (conversationBusy.current || !selected) return
    conversationBusy.current = true; setScopeBusy(true); setScopeError(false)
    try {
      const created = await agentRunsApi.createConversation(item?.name ?? t("artifact.workspace"), selected.scene)
      if (alive.current) { setConversations(values => [created, ...values]); select(created) }
    } catch { if (alive.current) setScopeError(true) }
    finally { conversationBusy.current = false; if (alive.current) setScopeBusy(false) }
  }
  const changeScope = async (value: string) => {
    if (!selected || conversationBusy.current) return
    conversationBusy.current = true; setScopeBusy(true); setScopeError(false)
    try {
      const saved = await agentRunsApi.updateConversation(selected.id, { ...selected.scene, datasource_ids: value === "none" ? [] : value === "authorized" ? null : [Number(value)] })
      if (alive.current) { setSelected(saved); setConversations(values => values.map(record => record.id === saved.id ? saved : record)) }
    } catch { if (alive.current) setScopeError(true) }
    finally { conversationBusy.current = false; if (alive.current) setScopeBusy(false) }
  }
  const object = (value: string): Record<string, unknown> => {
    try { const parsed = JSON.parse(value); if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed } catch { /* Show one actionable format error. */ }
    throw new Error(t("artifact.jsonObject"))
  }
  const operate = async (operation: () => Promise<unknown>, success: string) => {
    if (operationBusy.current) return
    operationBusy.current = true; setBusy(true); setOperationError(""); setNotice("")
    try {
      await operation()
      if (alive.current) setNotice(success)
    } catch (error) {
      if (alive.current) {
        const detail = isAxiosError(error) ? error.response?.data?.detail : error instanceof Error ? error.message : null
        setOperationError(typeof detail === "string" ? detail : t("artifact.operationFailed"))
      }
    } finally {
      await refresh(); operationBusy.current = false
      if (alive.current) setBusy(false)
    }
  }

  if (loadError) return <div role="alert"><p>{t("artifact.loadFailed")}</p><Button onClick={() => window.location.reload()}>{t("runtime.reload")}</Button><Link to="/function">{t("artifact.back")}</Link></div>
  if (!item || !draft || !selected) return <p role="status">{t("runtime.loading")}</p>
  const checkNames: Record<string, string> = { python_syntax: t("artifact.syntax"), entrypoint: t("artifact.entrypoint"), controlled_runtime: t("artifact.runtime") }
  const statuses: Record<string, string> = { passed: t("artifact.passed"), failed: t("artifact.failed"), unavailable: t("artifact.unavailable"), not_run: t("artifact.notRun") }
  const published = Boolean(draft.revision_id && draft.released_revision_id === draft.revision_id)
  const disabled = busy || stale || readonly
  return <div className="flex min-w-0 flex-col gap-4 lg:h-[calc(100dvh-8rem)] lg:min-h-[36rem]">
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-3">
      <div className="min-w-0"><Link to="/function" className="inline-flex min-h-11 items-center text-xs text-muted-foreground underline">{t("artifact.back")}</Link><h1 className="break-words text-xl font-semibold">{item.name}</h1><p className="mt-1 break-words text-sm text-muted-foreground">{item.description}</p></div>
      <Button variant="outline" className="min-h-11" disabled={scopeBusy} onClick={() => void createConversation()}>{t("runtime.newConversation")}</Button>
    </header>
    <div className="grid min-h-0 min-w-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(19rem,0.6fr)]">
      <div className="flex h-[70dvh] min-h-[30rem] min-w-0 flex-col lg:h-auto lg:min-h-0">
        <div className="flex flex-wrap gap-3">
          <label className="min-w-0 flex-1 text-xs text-muted-foreground">{t("runtime.conversation")}<select className={fieldClass} value={selected.id} disabled={scopeBusy} onChange={event => { const value = conversations.find(record => record.id === event.target.value); if (value) select(value) }}>{conversations.map(record => <option key={record.id} value={record.id}>{record.title} · {new Date(record.created_at * 1000).toLocaleTimeString(locale)}</option>)}</select></label>
          <label className="min-w-0 flex-1 text-xs text-muted-foreground">{t("runtime.datasourceScope")}<select className={fieldClass} disabled={scopeBusy} value={selected.scene.datasource_ids?.length === 0 ? "none" : selected.scene.datasource_ids?.length === 1 ? String(selected.scene.datasource_ids[0]) : "authorized"} onChange={event => void changeScope(event.target.value)}><option value="none">{t("runtime.noDatasource")}</option><option value="authorized">{t("runtime.authorizedDatasources")}</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
        </div>
        {scopeError && <p role="alert" className="text-sm text-destructive">{t("runtime.scopeWasNotSavedThePreviousScope")}</p>}
        {sourcesError && <p role="alert" className="text-xs text-muted-foreground">{t("runtime.datasourceListIsUnavailableYouCanStill")}</p>}
        <div className="min-h-0 flex-1"><RunConversationView conversationId={selected.id} scene={selected.scene} disabled={scopeBusy} onEvent={onEvent} /></div>
      </div>
      <aside aria-label={t("artifact.current")} className="min-w-0 space-y-5 border-t border-border pt-4 lg:overflow-y-auto lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{t("artifact.current")}</h2><Button variant="ghost" className="min-h-11" onClick={() => void refresh()}>{t("artifact.refresh")}</Button></div>
        {stale && <p role="alert" className="text-sm text-destructive">{t("artifact.refreshFailed")}</p>}
        {readonly && <p className="text-sm text-muted-foreground">{t("artifact.builtin")}</p>}
        <div className="space-y-2 text-sm" data-testid="artifact-state">
          <p>{published ? t("artifact.published") : t("artifact.unpublished")}</p>
          <p>{t("artifact.release")}: {draft.current_release_id ? `#${draft.current_release_id}` : t("artifact.noRelease")}</p>
          {draft.revision_id ? <><p className="break-all text-xs text-muted-foreground">{t("artifact.version")}: <span title={draft.revision_hash}>{draft.revision_id}</span></p><p>{t("artifact.changed")}: {draft.changed_files.join(", ") || t("artifact.unchanged")}</p></> : <p>{t("artifact.empty")}</p>}
        </div>
        <section aria-label={t("artifact.checks")} className="space-y-3 border-t border-border pt-4"><h3 className="text-sm font-medium">{t("artifact.checks")}</h3>
          {!draft.validation ? <p className="text-sm text-muted-foreground">{t("artifact.noChecks")}</p> : <ul className="space-y-3">{draft.validation.checks.map(check => <li key={check.name} className="text-sm"><div className="flex flex-wrap justify-between gap-2"><span>{checkNames[check.name] ?? check.name}</span><span>{statuses[check.status] ?? check.status}{check.executed ? ` · ${t("artifact.executed")}` : check.status !== "unavailable" && check.status !== "not_run" ? ` · ${t("artifact.notRun")}` : ""}</span></div>{check.diagnostic && <p className="mt-1 break-words text-xs text-muted-foreground">{check.diagnostic}</p>}</li>)}</ul>}
          <label className="block text-xs text-muted-foreground">{t("artifact.payload")}<textarea className={`${fieldClass} mt-1 font-mono`} value={payload} onChange={event => setPayload(event.target.value)} disabled={busy} rows={2} /></label>
          <div className="flex flex-wrap gap-2"><Button variant="outline" className="min-h-11" disabled={disabled || !draft.revision_id || Boolean(editor)} onClick={() => void operate(() => functionArtifactsApi.validate(id, { expected_revision: draft.revision_hash, payload: object(payload) }), t("artifact.checked"))}>{t("artifact.validate")}</Button><Button className="min-h-11" disabled={disabled || Boolean(editor) || published || !canPublishDraft(draft)} onClick={() => void operate(() => functionArtifactsApi.publish(id, { expected_revision: draft.revision_hash, validation_id: draft.validation!.id }), t("artifact.released"))}>{t("artifact.publish")}</Button></div>
          <p className="text-xs leading-5 text-muted-foreground">{t("artifact.publishHelp")}</p>
        </section>
        {busy && <p role="status" className="text-sm">{t("artifact.busy")}</p>}{notice && <p role="status" className="text-sm">{notice}</p>}{operationError && <p role="alert" className="break-words text-sm text-destructive">{operationError}</p>}
        {editor ? <div className="space-y-3"><p className="text-xs text-muted-foreground">{t("artifact.localChanges")}</p><label className="block text-sm">{t("artifact.sourceFile")}<textarea spellCheck={false} className={`${fieldClass} mt-1 min-h-64 font-mono text-xs`} value={editor.code} onChange={event => setEditor({ ...editor, code: event.target.value })} disabled={busy} /></label><label className="block text-sm">{t("artifact.manifestFile")}<textarea className={`${fieldClass} mt-1 font-mono text-xs`} value={editor.manifest} onChange={event => setEditor({ ...editor, manifest: event.target.value })} disabled={busy} /></label><div className="flex flex-wrap gap-2"><Button disabled={disabled} onClick={() => void operate(async () => { await functionArtifactsApi.save(id, { expected_revision: editor.revision, code: editor.code, dependencies: object(editor.manifest) }); if (alive.current) setEditor(null) }, t("artifact.saved"))}>{t("artifact.save")}</Button><Button variant="ghost" disabled={busy} onClick={() => setEditor(null)}>{t("artifact.discard")}</Button></div></div> : <><details><summary className="min-h-11 cursor-pointer py-3 text-sm">{t("artifact.sourceFile")}</summary><pre className="max-h-96 overflow-auto whitespace-pre text-xs leading-5">{draft.code}</pre></details><details><summary className="min-h-11 cursor-pointer py-3 text-sm">{t("artifact.manifestFile")}</summary><pre className="max-h-64 overflow-auto text-xs">{JSON.stringify(draft.dependencies, null, 2)}</pre></details><Button variant="outline" className="min-h-11" disabled={disabled} onClick={() => setEditor({ code: draft.code, manifest: JSON.stringify(draft.dependencies, null, 2), revision: draft.revision_hash })}>{t("artifact.edit")}</Button></>}
        <details className="border-t border-border pt-2"><summary className="min-h-11 cursor-pointer py-3 text-sm">{t("artifact.metadata")}</summary><form className="space-y-3" onSubmit={event => { event.preventDefault(); void operate(async () => { const updated = await functionsApi.update(id, { name, description }); if (alive.current) setItem(updated) }, t("artifact.metadataSaved")) }}><label className="block text-xs">{t("artifact.name")}<input className={fieldClass} value={name} maxLength={255} required disabled={disabled} onChange={event => setName(event.target.value)} /></label><label className="block text-xs">{t("artifact.description")}<textarea className={fieldClass} value={description} disabled={disabled} onChange={event => setDescription(event.target.value)} /></label><Button variant="outline" disabled={disabled || !name.trim()}>{t("artifact.saveMetadata")}</Button></form></details>
      </aside>
    </div>
  </div>
}
