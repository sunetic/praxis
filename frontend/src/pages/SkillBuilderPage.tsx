import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ArrowLeft, Loader2, Save, Sparkles } from "lucide-react"
import { toast } from "sonner"

import { ChatThreadView } from "@/components/chat/ChatThreadView"
import { useChatController } from "@/components/chat/useChatController"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useShellI18n } from "@/i18n/shellI18n"
import { skillsApi } from "@/lib/api"
import type { SceneAgentPayload } from "@/lib/api"

const SCOPE_OPTIONS = ["general", "mysql", "postgresql"] as const

type SkillDraft = {
  name: string
  description: string
  database: string
  always_apply: boolean
  prompt: string
}

const EMPTY_DRAFT: SkillDraft = {
  name: "",
  description: "",
  database: "general",
  always_apply: false,
  prompt: "",
}

function extractSkillResult(text: string): SkillDraft | null {
  const fenceMatch = text.match(/```(?:json)?\s*\n?\s*(\{[\s\S]*?"skill_result"[\s\S]*?\})\s*\n?\s*```/)
  const raw = fenceMatch ? fenceMatch[1] : null
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    const sr = parsed.skill_result
    if (!sr || typeof sr !== "object") return null
    return {
      name: String(sr.name || ""),
      description: String(sr.description || ""),
      database: SCOPE_OPTIONS.includes(sr.database) ? sr.database : "general",
      always_apply: sr.always_apply === true,
      prompt: String(sr.prompt || ""),
    }
  } catch {
    return null
  }
}

function displayScope(db: string, t: ReturnType<typeof useShellI18n>["t"]): string {
  if (db === "general") return t("skills.scope.general")
  if (db === "mysql") return t("skills.scope.mysql")
  if (db === "postgresql") return t("skills.scope.postgresql")
  return db
}

export function SkillBuilderPage() {
  const { t } = useShellI18n()
  const navigate = useNavigate()

  const [draft, setDraft] = useState<SkillDraft>(EMPTY_DRAFT)
  const [saving, setSaving] = useState(false)
  const [sessionKey] = useState(() => `skill-builder-${Date.now()}`)
  const lastAppliedRef = useRef<string>("")

  const sceneAgentPayload = useMemo<SceneAgentPayload>(
    () => ({
      key: "skill_builder",
      context: {},
      focus_object: null,
      tools: [],
      skills: [],
    }),
    []
  )

  const controller = useChatController({
    title: "Skill Builder",
    datasourceId: null,
    sceneAgentPayload,
    sceneConversationMeta: { sceneKey: "skill_builder" },
    fetchOnConversationChange: true,
    freshSessionKey: sessionKey,
  })

  useEffect(() => {
    const msgs = controller.messages
    if (!msgs.length) return
    for (let i = msgs.length - 1; i >= 0; i--) {
      const msg = msgs[i]
      if (msg.role !== "assistant") continue
      const text = msg.content || ""
      const result = extractSkillResult(text)
      if (result) {
        const key = JSON.stringify(result)
        if (key !== lastAppliedRef.current) {
          lastAppliedRef.current = key
          setDraft(result)
        }
        break
      }
    }
  }, [controller.messages])

  const handleSave = useCallback(async () => {
    if (!draft.name.trim() || !draft.description.trim() || !draft.prompt.trim()) {
      toast.error(t("skills.validate.required"))
      return
    }
    setSaving(true)
    try {
      await skillsApi.create({
        name: draft.name.trim(),
        version: "1.0.0",
        description: draft.description.trim(),
        database: draft.database,
        always_apply: draft.always_apply,
        prompt: draft.prompt.trim(),
      })
      toast.success(t("skills.builder.saved"))
      navigate("/skills")
    } catch {
      toast.error(t("skills.builder.saveFailed"))
    } finally {
      setSaving(false)
    }
  }, [draft, navigate, t])

  const hasDraft = Boolean(draft.name || draft.prompt)

  return (
    <div className="flex h-[calc(100vh-4.5rem)] min-h-0 flex-col gap-3 bg-background p-2 md:p-3 animate-in fade-in duration-500">
      <div className="flex items-center justify-between">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                <Link to="/skills">{t("sidebar.nav.skill")}</Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>{t("skills.builder.pageTitle")}</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => navigate("/skills")}>
            <ArrowLeft className="size-4" />
            {t("skills.builder.backToList")}
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving || !hasDraft}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            {saving ? t("skills.builder.saving") : t("skills.builder.saveDraft")}
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="flex min-h-0 flex-col rounded-xl border border-border bg-card shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-500">
          <div className="shrink-0 border-b border-border px-4 py-3">
            <div className="flex items-center gap-2">
              <Sparkles className="size-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">{t("skills.builder.editorTitle")}</h2>
              {hasDraft ? (
                <Badge variant="secondary" className="text-[10px]">
                  {draft.database !== "general" ? displayScope(draft.database, t) : null}
                </Badge>
              ) : null}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="builder-name" className="text-sm font-medium">
                {t("skills.form.name")}
              </label>
              <Input
                id="builder-name"
                value={draft.name}
                onChange={(e) => setDraft((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="slow-query-diagnosis"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="builder-description" className="text-sm font-medium">
                {t("skills.form.description")}
              </label>
              <Input
                id="builder-description"
                value={draft.description}
                onChange={(e) => setDraft((prev) => ({ ...prev, description: e.target.value }))}
                placeholder={t("skills.form.descPlaceholder")}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-sm font-medium">{t("skills.form.scope")}</label>
                <Select
                  value={draft.database}
                  onValueChange={(v) => setDraft((prev) => ({ ...prev, database: v }))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SCOPE_OPTIONS.map((db) => (
                      <SelectItem key={db} value={db}>
                        {displayScope(db, t)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-end pb-2">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="builder-always-apply"
                    checked={draft.always_apply}
                    onCheckedChange={(checked) =>
                      setDraft((prev) => ({ ...prev, always_apply: checked === true }))
                    }
                  />
                  <label htmlFor="builder-always-apply" className="text-sm text-muted-foreground">
                    {t("skills.form.alwaysApply")}
                  </label>
                </div>
              </div>
            </div>
            <div className="space-y-1.5 flex-1">
              <label htmlFor="builder-prompt" className="text-sm font-medium">
                {t("skills.form.prompt")}
              </label>
              <Textarea
                id="builder-prompt"
                value={draft.prompt}
                onChange={(e) => setDraft((prev) => ({ ...prev, prompt: e.target.value }))}
                className="min-h-[300px] max-h-[60vh] resize-y overflow-y-auto font-mono text-sm"
                placeholder={t("skills.form.promptPlaceholder")}
              />
            </div>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col rounded-xl border border-border bg-card shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-500">
          <ChatThreadView
            controller={controller}
            title={t("skills.builder.title")}
            placeholder={t("skills.builder.chatPlaceholder")}
            embedded
            showHeader
            enableSaveAsAgent={false}
            enableHandoff={false}
            enableBatchActions={false}
            className="flex-1"
          />
        </aside>
      </div>
    </div>
  )
}
