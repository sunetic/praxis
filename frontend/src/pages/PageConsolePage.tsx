import { useCallback, useEffect, useRef, useState } from "react"
import { isAxiosError } from "axios"
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom"
import { RunConversationView } from "@/components/chat/RunConversationView"
import { PagePreviewRenderer } from "@/components/page/PagePreviewRenderer"
import { Button } from "@/components/ui/button"
import { useShellI18n } from "@/i18n/shellI18nContext"
import { datasourcesApi, functionsApi, pagesApi, type DataSource } from "@/lib/api"
import { agentRunsApi, type RunConversation, type RunEvent, type RunScene, type RunView } from "@/lib/agentRuns"
import { canPublishPage, pageArtifactsApi, type PageDraft, type PagePreview, type PageSource } from "@/lib/pageArtifacts"

type PageRecord = { id: number; name: string; description?: string | null }
type Editor = { revision: string; files: string; bindings: string }
const fieldClass = "min-h-11 w-full rounded-md border border-input bg-background p-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"

export function PageConsolePage() {
  const { pageId } = useParams()
  return pageId ? <PageWorkspace key={pageId} id={Number(pageId)} /> : <Navigate to="/page" replace />
}

function PageWorkspace({ id }: { id: number }) {
  const { t, locale } = useShellI18n()
  const [params, setParams] = useSearchParams()
  const [record, setRecord] = useState<PageRecord | null>(null)
  const [draft, setDraft] = useState<PageDraft | null>(null)
  const [preview, setPreview] = useState<PagePreview | null>(null)
  const [previewError, setPreviewError] = useState(false)
  const [conversations, setConversations] = useState<RunConversation[]>([])
  const [selected, setSelected] = useState<RunConversation | null>(null)
  const [sources, setSources] = useState<DataSource[]>([])
  const [sourcesError, setSourcesError] = useState(false)
  const [functions, setFunctions] = useState<{ id: number; name: string }[]>([])
  const [functionsError, setFunctionsError] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const [stale, setStale] = useState(false)
  const [scopeBusy, setScopeBusy] = useState(false)
  const [scopeError, setScopeError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [editor, setEditor] = useState<Editor | null>(null)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const initialized = useRef<Promise<[PageRecord, PageDraft, RunConversation[]]> | null>(null)
  const alive = useRef(true)
  const version = useRef(0)
  const operationBusy = useRef(false)
  const conversationBusy = useRef(false)

  useEffect(() => {
    alive.current = true
    let active = true
    if (!Number.isSafeInteger(id) || id < 1) { setLoadError(true); return }
    initialized.current ??= Promise.all([
      pagesApi.get(id), pageArtifactsApi.read(id),
      agentRunsApi.conversations().then(async records => {
        const matching = records.filter(value => value.scene.page_ids?.length === 1 && value.scene.page_ids[0] === id)
        if (params.get("conversationId") && !matching.some(value => value.id === params.get("conversationId"))) throw new Error("Conversation scope mismatch")
        return matching.length ? matching : [await agentRunsApi.createConversation(t("page.workspace"), { page_ids: [id], datasource_ids: [] })]
      }),
    ])
    void initialized.current.then(([item, current, records]) => {
      if (!active) return
      setRecord(item); setDraft(current); setName(item.name); setDescription(item.description ?? ""); setConversations(records)
      setSelected(records.find(value => value.id === params.get("conversationId")) ?? records[0])
    }).catch(() => { if (active) setLoadError(true) })
    void datasourcesApi.list().then(values => { if (active) setSources(values.filter(source => source.status === "active")) }).catch(() => { if (active) setSourcesError(true) })
    void functionsApi.list().then(values => { if (active) setFunctions(values) }).catch(() => { if (active) setFunctionsError(true) })
    return () => { active = false; alive.current = false }
    // Share one initialization across StrictMode effect replays.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // A newly saved source never inherits the previous iframe/compilation.
  useEffect(() => {
    let active = true
    setPreview(current => current && current.revision_id === draft?.revision_id && current.artifact_hash === draft?.artifact_hash ? current : null); setPreviewError(false)
    if (draft?.revision_id && draft.artifact_hash) {
      void pageArtifactsApi.preview(id, draft.revision_id).then(value => {
        if (active) {
          if (value.revision_id !== draft.revision_id || value.artifact_hash !== draft.artifact_hash) setPreviewError(true)
          else setPreview(value)
        }
      }).catch(() => { if (active) setPreviewError(true) })
    }
    return () => { active = false }
  }, [id, draft])

  const refresh = useCallback(async () => {
    const requested = ++version.current
    try {
      const current = await pageArtifactsApi.read(id)
      if (alive.current && requested === version.current) { setDraft(current); setStale(false) }
    } catch { if (alive.current && requested === version.current) setStale(true) }
  }, [id])
  const onEvent = useCallback((event: RunEvent, view: RunView) => {
    const call = event.type === "tool_result" ? view.blocks.find(block => block.kind === "tool" && block.id === event.call_id) : undefined
    const changed = call?.kind === "tool" && ["page_write", "page_edit", "page_validate", "page_publish", "page_create_function", "function_write", "function_edit", "function_validate", "function_publish"].includes(call.name)
    if (changed || ["run_finished", "run_failed", "run_cancelled"].includes(event.type)) void refresh()
  }, [refresh])
  const select = (value: RunConversation) => { setSelected(value); setScopeError(false); setParams({ conversationId: value.id }, { replace: true }) }
  const updateConversation = async (newConversation: boolean, patch?: Partial<RunScene>) => {
    if (!selected || conversationBusy.current) return
    conversationBusy.current = true; setScopeBusy(true); setScopeError(false)
    try {
      const value = newConversation ? await agentRunsApi.createConversation(record?.name ?? t("page.workspace"), selected.scene)
        : await agentRunsApi.updateConversation(selected.id, { ...selected.scene, ...patch })
      if (alive.current) { setConversations(values => newConversation ? [value, ...values] : values.map(item => item.id === value.id ? value : item)); select(value) }
    } catch { if (alive.current) setScopeError(true) }
    finally { conversationBusy.current = false; if (alive.current) setScopeBusy(false) }
  }
  const operate = async (operation: () => Promise<unknown>, success: string) => {
    if (operationBusy.current) return
    operationBusy.current = true; setBusy(true); setError(""); setNotice("")
    try { await operation(); if (alive.current) setNotice(success) }
    catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : error instanceof Error ? error.message : null
      if (alive.current) setError(typeof detail === "string" ? detail : t("artifact.operationFailed"))
    } finally { await refresh(); operationBusy.current = false; if (alive.current) setBusy(false) }
  }
  const source = (edit: Editor): PageSource => {
    try {
      const files = JSON.parse(edit.files), bindings = JSON.parse(edit.bindings)
      if (!files || typeof files !== "object" || Array.isArray(files) || Object.values(files).some(value => typeof value !== "string") || !bindings || typeof bindings !== "object" || Array.isArray(bindings)) throw new Error()
      return { files, bindings }
    } catch { throw new Error(t("page.sourceFormat")) }
  }

  if (loadError) return <div role="alert"><p>{t("artifact.loadFailed")}</p><Button onClick={() => window.location.reload()}>{t("runtime.reload")}</Button><Link to="/page">{t("page.back")}</Link></div>
  if (!record || !draft || !selected) return <p role="status">{t("runtime.loading")}</p>
  const published = Boolean(draft.revision_id && draft.released_revision_id === draft.revision_id)
  const disabled = busy || stale
  const checkNames: Record<string, string> = { source_compile: t("page.compile"), function_bindings: t("page.bindingCheck"), browser_runtime: t("page.browserCheck"), binding_runtime: t("page.bindingRuntime") }
  const statuses: Record<string, string> = { passed: t("artifact.passed"), failed: t("artifact.failed"), unavailable: t("artifact.unavailable"), not_run: t("artifact.notRun") }
  return <div className="flex min-w-0 flex-col gap-4 lg:h-[calc(100dvh-8rem)] lg:min-h-[36rem]">
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-3">
      <div className="min-w-0"><Link to="/page" className="inline-flex min-h-11 items-center text-xs text-muted-foreground underline">{t("page.back")}</Link><h1 className="break-words text-xl font-semibold">{record.name}</h1><p className="mt-1 text-sm text-muted-foreground">{record.description}</p></div>
      <div className="flex flex-wrap items-center gap-4">{draft.current_release_id && <Link to={`/page/${id}`} className="inline-flex min-h-11 items-center text-sm underline">{t("page.published")}</Link>}<Button variant="outline" disabled={scopeBusy} onClick={() => void updateConversation(true)}>{t("runtime.newConversation")}</Button></div>
    </header>
    <div className="grid min-h-0 min-w-0 flex-1 gap-5 lg:grid-cols-[minmax(20rem,0.85fr)_minmax(0,1.15fr)]">
      <div className="flex h-[70dvh] min-h-[30rem] min-w-0 flex-col lg:h-auto lg:min-h-0">
        <div className="flex flex-wrap gap-3">
          <label className="min-w-0 flex-1 text-xs text-muted-foreground">{t("runtime.conversation")}<select className={fieldClass} value={selected.id} disabled={scopeBusy} onChange={event => { const value = conversations.find(item => item.id === event.target.value); if (value) select(value) }}>{conversations.map(item => <option key={item.id} value={item.id}>{item.title} · {new Date(item.created_at * 1000).toLocaleTimeString(locale)}</option>)}</select></label>
          <label className="min-w-0 flex-1 text-xs text-muted-foreground">{t("runtime.datasourceScope")}<select className={fieldClass} disabled={scopeBusy} value={selected.scene.datasource_ids?.length === 0 ? "none" : selected.scene.datasource_ids?.length === 1 ? String(selected.scene.datasource_ids[0]) : "authorized"} onChange={event => void updateConversation(false, { datasource_ids: event.target.value === "none" ? [] : event.target.value === "authorized" ? null : [Number(event.target.value)] })}><option value="none">{t("runtime.noDatasource")}</option><option value="authorized">{t("runtime.authorizedDatasources")}</option>{sources.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        </div>
        {scopeError && <p role="alert" className="text-sm text-destructive">{t("runtime.scopeWasNotSavedThePreviousScope")}</p>}
        {sourcesError && <p role="alert" className="text-xs text-muted-foreground">{t("runtime.datasourceListIsUnavailableYouCanStill")}</p>}
        <details className="text-xs"><summary className="min-h-11 cursor-pointer py-3">{t("page.functionScope")}</summary><p className="mb-2 text-muted-foreground">{t("page.functionScopeHelp")}</p>{functionsError ? <p role="alert">{t("page.functionListFailed")}</p> : <label>{t("page.functionScope")}<select multiple size={3} className={fieldClass} disabled={scopeBusy} value={(selected.scene.function_ids ?? []).map(String)} onChange={event => void updateConversation(false, { function_ids: Array.from(event.target.selectedOptions, option => Number(option.value)) })}>{functions.map(item => <option key={item.id} value={item.id}>{item.name} · #{item.id}</option>)}</select></label>}</details>
        <div className="min-h-0 flex-1"><RunConversationView conversationId={selected.id} scene={selected.scene} disabled={scopeBusy} onEvent={onEvent} /></div>
      </div>
      <aside aria-label={t("artifact.current")} className="min-w-0 space-y-5 border-t border-border pt-4 lg:overflow-y-auto lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
        <div className="flex items-center justify-between gap-2"><h2 className="font-semibold">{t("artifact.current")}</h2><Button variant="ghost" onClick={() => void refresh()}>{t("artifact.refresh")}</Button></div>
        {stale && <p role="alert" className="text-sm text-destructive">{t("artifact.refreshFailed")}</p>}
        <div className="space-y-2 text-sm" data-testid="artifact-state"><p>{published ? t("artifact.published") : t("artifact.unpublished")}</p><p>{t("artifact.release")}: {draft.current_release_id ? `#${draft.current_release_id}` : t("artifact.noRelease")}</p><p className="break-all text-xs text-muted-foreground">{t("artifact.version")}: {draft.revision_id ?? t("artifact.empty")}</p><p>{t("artifact.changed")}: {draft.changed_files.join(", ") || t("artifact.unchanged")}</p>{draft.bindings_changed && <p>{t("page.bindingChanged")}</p>}</div>
        <section aria-label={t("artifact.checks")} className="space-y-3 border-t border-border pt-4"><h3 className="text-sm font-medium">{t("artifact.checks")}</h3>
          {!draft.validation ? <p className="text-sm text-muted-foreground">{t("artifact.noChecks")}</p> : <ul className="space-y-3">{draft.validation.checks.map(check => <li key={check.name} className="text-sm"><div className="flex flex-wrap justify-between gap-2"><span>{checkNames[check.name] ?? check.name}</span><span>{check.applicable === false ? t("page.notApplicable") : statuses[check.status] ?? check.status}{check.executed ? ` · ${t("artifact.executed")}` : ""}</span></div>{check.diagnostic && <p className="mt-1 break-words text-xs text-muted-foreground">{check.diagnostic}</p>}</li>)}</ul>}
          <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={disabled || !draft.revision_id || Boolean(editor)} onClick={() => void operate(() => pageArtifactsApi.validate(id, draft.revision_hash), t("artifact.checked"))}>{t("artifact.validate")}</Button><Button disabled={disabled || Boolean(editor) || published || !canPublishPage(draft)} onClick={() => void operate(() => pageArtifactsApi.publish(id, { expected_revision: draft.revision_hash, validation_id: draft.validation!.id }), t("artifact.released"))}>{t("artifact.publish")}</Button></div><p className="text-xs leading-5 text-muted-foreground">{t("artifact.publishHelp")}</p>
        </section>
        {busy && <p role="status">{t("artifact.busy")}</p>}{notice && <p role="status" className="text-sm">{notice}</p>}{error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
        <section aria-label={t("page.preview")} className="space-y-3 border-t border-border pt-4"><h3 className="text-sm font-medium">{t("page.preview")}</h3><p className="text-xs leading-5 text-muted-foreground">{t("page.previewHelp")}</p>{Object.keys(draft.bindings).length > 0 && <p className="text-xs text-muted-foreground">{t("page.bindingUnavailable")}</p>}
          {previewError ? <p role="alert" className="text-sm text-destructive">{t("page.previewFailed")}</p> : preview && preview.revision_id === draft.revision_id && preview.artifact_hash === draft.artifact_hash ? <PagePreviewRenderer key={preview.artifact_hash} html={preview.html} title={t("page.preview")} /> : <p className="py-8 text-sm text-muted-foreground">{draft.artifact_hash ? t("runtime.loading") : t("page.noPreview")}</p>}
        </section>
        <details className="border-t border-border pt-2"><summary className="min-h-11 cursor-pointer py-3 text-sm">{t("page.dependencies")}</summary><div className="space-y-3 text-sm">{Object.keys(draft.bindings).length === 0 ? <p>{t("page.noDependencies")}</p> : Object.entries(draft.bindings).map(([name, binding]) => <p key={name} className="break-all"><Link className="underline" to={`/function/${binding.function_id}/build`}>{name} · #{binding.function_id}</Link><br />{binding.release_id ? `${t("artifact.release")}: #${binding.release_id}` : `${t("artifact.version")}: ${binding.revision_id}`}</p>)}{draft.owned_functions.length > 0 && <><h4>{t("page.ownedFunctions")}</h4>{draft.owned_functions.map(item => <Link key={item.id} to={`/function/${item.id}/build`} className="block min-h-11 underline">{item.name}</Link>)}</>}</div></details>
        {editor ? <div className="space-y-3"><p className="text-xs text-muted-foreground">{t("artifact.localChanges")}</p><label className="block text-sm">{t("page.files")}<textarea className={`${fieldClass} mt-1 min-h-64 font-mono text-xs`} spellCheck={false} value={editor.files} disabled={busy} onChange={event => setEditor({ ...editor, files: event.target.value })} /></label><label className="block text-sm">{t("page.bindings")}<textarea className={`${fieldClass} mt-1 font-mono text-xs`} value={editor.bindings} disabled={busy} onChange={event => setEditor({ ...editor, bindings: event.target.value })} /></label><div className="flex flex-wrap gap-2"><Button disabled={disabled} onClick={() => void operate(async () => { await pageArtifactsApi.save(id, { expected_revision: editor.revision, source: source(editor) }); if (alive.current) setEditor(null) }, t("artifact.saved"))}>{t("artifact.save")}</Button><Button variant="ghost" disabled={busy} onClick={() => setEditor(null)}>{t("artifact.discard")}</Button></div></div> : <><details><summary className="min-h-11 cursor-pointer py-3 text-sm">{t("page.source")}</summary>{Object.entries(draft.files).map(([name, content]) => <details key={name}><summary className="min-h-11 cursor-pointer py-3 text-xs">{name}</summary><pre className="max-h-96 overflow-auto whitespace-pre text-xs leading-5">{content}</pre></details>)}</details><Button variant="outline" disabled={disabled} onClick={() => setEditor({ revision: draft.revision_hash, files: JSON.stringify(draft.files, null, 2), bindings: JSON.stringify(draft.bindings, null, 2) })}>{t("page.edit")}</Button></>}
        <details className="border-t border-border pt-2"><summary className="min-h-11 cursor-pointer py-3 text-sm">{t("artifact.metadata")}</summary><form className="space-y-3" onSubmit={event => { event.preventDefault(); void operate(async () => { const value = await pagesApi.update(id, { name, description }); if (alive.current) setRecord(value) }, t("artifact.metadataSaved")) }}><label className="block text-xs">{t("artifact.name")}<input className={fieldClass} value={name} maxLength={255} required disabled={disabled} onChange={event => setName(event.target.value)} /></label><label className="block text-xs">{t("artifact.description")}<textarea className={fieldClass} value={description} disabled={disabled} onChange={event => setDescription(event.target.value)} /></label><Button variant="outline" disabled={disabled || !name.trim()}>{t("artifact.saveMetadata")}</Button></form></details>
      </aside>
    </div>
  </div>
}
