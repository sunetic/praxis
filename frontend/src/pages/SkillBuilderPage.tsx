import { useCallback, useEffect, useRef, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { isAxiosError } from "axios"
import { toast } from "sonner"
import { RunConversationView } from "@/components/chat/RunConversationView"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useShellI18n } from "@/i18n/shellI18n"
import { skillsApi } from "@/lib/api"
import { agentRunsApi, type RunConversation, type RunEvent } from "@/lib/agentRuns"
import { skillDraftsApi, type SkillDraft, type SkillDraftContent } from "@/lib/skillDrafts"

export function SkillBuilderPage() {
  const [params] = useSearchParams()
  const createdHere = useRef<string | null>(null)
  const id = params.get("draftId")
  return <SkillBuilderWorkspace key={!id || id === createdHere.current ? "new" : id} onCreated={value => { createdHere.current = value }} />
}

function SkillBuilderWorkspace({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useShellI18n()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [record, setRecord] = useState<SkillDraft | null>(null)
  const [draft, setDraft] = useState<SkillDraftContent | null>(null)
  const [conversation, setConversation] = useState<RunConversation | null>(null)
  const [error, setError] = useState("")
  const [stale, setStale] = useState(false)
  const [saving, setSaving] = useState(false)
  const initialized = useRef<Promise<[SkillDraft, RunConversation]> | null>(null)
  const dirty = useRef(false)
  const busy = useRef(false)
  const alive = useRef(true)
  const refreshSequence = useRef(0)

  useEffect(() => {
    alive.current = true
    let active = true
    initialized.current ??= (async () => {
      const current = params.get("draftId") ? await skillDraftsApi.read(params.get("draftId")!) : await skillDraftsApi.create()
      const conversationId = params.get("conversationId")
      let session: RunConversation
      if (conversationId) {
        const existing = (await agentRunsApi.conversations()).find(item => item.id === conversationId)
        if (!existing || existing.scene.skill_draft_ids?.length !== 1 || existing.scene.skill_draft_ids[0] !== current.id) throw new Error(t("skills.builder.scopeMismatch"))
        session = existing
      } else {
        session = await agentRunsApi.createConversation(t("skills.builder.title"), {
          skill_draft_ids: [current.id], datasource_ids: [], knowledge_base_ids: [], service_ids: [],
        })
      }
      return [current, session] as [SkillDraft, RunConversation]
    })()
    void initialized.current.then(([current, session]) => {
      if (!active) return
      setRecord(current); setDraft(current.content); setConversation(session)
      if (!params.get("draftId")) onCreated(current.id)
      setParams({ draftId: current.id, conversationId: session.id }, { replace: true })
    }).catch(() => { if (active) setError(t("skills.builder.loadFailed")) })
    return () => { active = false; alive.current = false }
    // One workspace initialization, shared across StrictMode effect replays.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refresh = useCallback(async (discardLocal = false) => {
    if (!record || busy.current) return
    const sequence = ++refreshSequence.current
    try {
      const latest = await skillDraftsApi.read(record.id)
      if (!alive.current || sequence !== refreshSequence.current) return
      if (dirty.current && !discardLocal) {
        if (latest.revision !== record.revision) setStale(true)
      } else {
        dirty.current = false; setRecord(latest); setDraft(latest.content); setStale(false); setError("")
      }
    } catch { if (alive.current) { setError(t("skills.builder.loadFailed")); setStale(true) } }
  }, [record, t])
  const onEvent = useCallback((event: RunEvent) => {
    if (["tool_result", "run_finished", "run_failed", "run_cancelled"].includes(event.type)) void refresh()
  }, [refresh])
  const edit = (change: Partial<SkillDraftContent>) => {
    dirty.current = true
    setDraft(value => value ? { ...value, ...change } : value)
  }
  const save = async (install: boolean) => {
    if (!record || !draft || busy.current || stale) return
    busy.current = true; refreshSequence.current++; setSaving(true); setError("")
    try {
      const saved = dirty.current ? await skillDraftsApi.write(record.id, record.revision, draft) : record
      if (alive.current) { dirty.current = false; setRecord(saved); setDraft(saved.content) }
      if (install) {
        await skillsApi.create(saved.content)
        if (alive.current) { toast.success(t("skills.builder.saved")); navigate("/skills") }
      } else if (alive.current) toast.success(t("skills.builder.draftSaved"))
    } catch (cause) {
      if (alive.current) {
        if (isAxiosError(cause) && cause.response?.status === 409) setStale(true)
        const detail = isAxiosError(cause) ? cause.response?.data?.detail : null
        setError(typeof detail === "string" ? detail : t("skills.builder.saveFailed"))
      }
    } finally { busy.current = false; if (alive.current) setSaving(false) }
  }

  if (!draft || !record || !conversation) return <div className="p-4">
    {error ? <><p role="alert">{error}</p><Button onClick={() => window.location.reload()}>{t("runtime.reload")}</Button></> : <p role="status">{t("runtime.loading")}</p>}
    <Link to="/skills" className="block py-3 underline">{t("skills.builder.backToList")}</Link>
  </div>
  return <div className="flex min-w-0 flex-col gap-4 lg:h-[calc(100dvh-8rem)] lg:min-h-[36rem]">
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-3">
      <div><Link className="inline-flex min-h-11 items-center text-xs underline" to="/skills">{t("skills.builder.backToList")}</Link><h1 className="text-xl font-semibold">{t("skills.builder.pageTitle")}</h1></div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" className="min-h-11" disabled={saving || stale} onClick={() => void save(false)}>{t("skills.builder.persistDraft")}</Button>
        <Button className="min-h-11" disabled={saving || stale || !draft.name.trim() || !draft.prompt.trim()} onClick={() => void save(true)}>{saving ? t("skills.builder.saving") : t("skills.builder.install")}</Button>
      </div>
    </header>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="grid min-h-0 min-w-0 flex-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.7fr)]">
      <section aria-label={t("skills.builder.title")} className="h-[65dvh] min-h-[28rem] min-w-0 lg:h-auto lg:min-h-0">
        <RunConversationView conversationId={conversation.id} scene={conversation.scene} onEvent={onEvent} disabled={saving} />
      </section>
      <aside className="min-w-0 space-y-4 border-t border-border pt-4 lg:overflow-y-auto lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0" aria-label={t("skills.builder.editorTitle")}>
        <h2 className="font-semibold">{t("skills.builder.editorTitle")}</h2>
        <p className="text-sm text-muted-foreground">{t("skills.builder.draftNotice")}</p>
        {stale && <div role="alert" className="space-y-2 text-sm"><p>{t("skills.builder.conflict")}</p><Button variant="outline" onClick={() => void refresh(true)}>{t("skills.builder.loadLatest")}</Button></div>}
        <fieldset disabled={saving} className="space-y-4 disabled:opacity-60">
          <div className="space-y-1"><label htmlFor="builder-name">{t("skills.form.name")}</label><Input id="builder-name" value={draft.name} onChange={event => edit({ name: event.target.value })} /></div>
          <div className="space-y-1"><label htmlFor="builder-description">{t("skills.form.description")}</label><Input id="builder-description" value={draft.description} onChange={event => edit({ description: event.target.value })} /></div>
          <div className="space-y-1"><label htmlFor="builder-version">{t("skills.builder.version")}</label><Input id="builder-version" value={draft.version} onChange={event => edit({ version: event.target.value })} /></div>
          <div className="space-y-1"><label htmlFor="builder-scope">{t("skills.form.scope")}</label><select id="builder-scope" className="min-h-11 w-full rounded-md border border-input bg-background px-2" value={draft.database} onChange={event => edit({ database: event.target.value })}>
            <option value="general">{t("skills.scope.general")}</option><option value="mysql">{t("skills.scope.mysql")}</option><option value="postgresql">{t("skills.scope.postgresql")}</option><option value="oceanbase">{t("skills.scope.oceanbase")}</option>
          </select></div>
          <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={draft.always_apply} onChange={event => edit({ always_apply: event.target.checked })} />{t("skills.form.alwaysApply")}</label>
          <div className="space-y-1"><label htmlFor="builder-prompt">{t("skills.form.prompt")}</label><Textarea id="builder-prompt" className="min-h-80 max-h-[60dvh] resize-y overflow-y-auto" value={draft.prompt} onChange={event => edit({ prompt: event.target.value })} /></div>
        </fieldset>
      </aside>
    </div>
  </div>
}
