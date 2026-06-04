import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, Loader2, Pencil, Plus, Search, Sparkles, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { DetailDrawer } from "@/components/shared/DetailDrawer"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { useShellI18n } from "@/i18n/shellI18n"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { skillsApi } from "@/lib/api"
import type { Skill, SkillInput } from "@/lib/api"

type SkillFormData = SkillInput

const EMPTY_FORM: SkillFormData = {
  name: "",
  version: "1.0.0",
  description: "",
  database: "general",
  always_apply: false,
  prompt: "",
}

const PAGE_SIZE = 10

export function SkillsPage() {
  const { t } = useShellI18n()
  const [skills, setSkills] = useState<Skill[]>([])
  const [query, setQuery] = useState("")
  const [sourceFilter, setSourceFilter] = useState<"all" | "built_in" | "custom">("all")
  const [databaseFilter, setDatabaseFilter] = useState<string>("all")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [editingSkillName, setEditingSkillName] = useState<string | null>(null)
  const [targetDelete, setTargetDelete] = useState<Skill | null>(null)
  const [detailSkill, setDetailSkill] = useState<Skill | null>(null)
  const [formData, setFormData] = useState<SkillFormData>(EMPTY_FORM)

  const fetchSkills = async (keyword?: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await skillsApi.list({ query: keyword?.trim() || undefined })
      setSkills(data)
    } catch {
      setError(t("skills.loadFailed"))
      toast.error(t("skills.loadFailed"))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSkills()
  }, [])

  const handleSourceFilterChange = (value: "all" | "built_in" | "custom") => {
    if (value === sourceFilter) return
    setLoading(true)
    setSkills([])
    setSourceFilter(value)
    void fetchSkills(query)
  }

  const handleDatabaseFilterChange = (value: string) => {
    if (value === databaseFilter) return
    setLoading(true)
    setSkills([])
    setDatabaseFilter(value)
    void fetchSkills(query)
  }

  const databaseOptions = useMemo(() => {
    const values = Array.from(new Set(skills.map((s) => s.database).filter(Boolean))).sort()
    return values
  }, [skills])

  const visibleSkills = useMemo(() => {
    const q = query.trim().toLowerCase()
    return skills.filter((item) => {
      if (sourceFilter !== "all" && item.source !== sourceFilter) return false
      if (databaseFilter !== "all" && item.database !== databaseFilter) return false
      if (!q) return true
      return `${item.name} ${item.description}`.toLowerCase().includes(q)
    })
  }, [query, skills, sourceFilter, databaseFilter])

  const pagedSkills = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return visibleSkills.slice(start, start + PAGE_SIZE)
  }, [page, visibleSkills])

  useEffect(() => {
    setPage(1)
  }, [query, sourceFilter, databaseFilter])

  const handleOpenCreate = () => {
    setEditingSkillName(null)
    setFormData(EMPTY_FORM)
    setFormOpen(true)
  }

  const handleOpenEdit = (skill: Skill) => {
    if (skill.source !== "custom") return
    setEditingSkillName(skill.name)
    setFormData({
      name: skill.name,
      version: skill.version,
      description: skill.description,
      database: skill.database,
      always_apply: skill.always_apply,
      prompt: skill.prompt,
    })
    setFormOpen(true)
  }

  const handleSave = async () => {
    if (!formData.name.trim() || !formData.description.trim() || !formData.prompt.trim()) {
      toast.error(t("skills.validate.required"))
      return
    }
    setSaving(true)
    try {
      if (editingSkillName) {
        await skillsApi.update(editingSkillName, formData)
      } else {
        await skillsApi.create(formData)
      }
      await fetchSkills(query)
      setFormOpen(false)
      toast.success(editingSkillName ? t("skills.toast.updated") : t("skills.toast.created"))
    } catch (err: unknown) {
      toast.error(extractApiErrorMessage(err, t("skills.toast.saveFailed")))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!targetDelete) return
    setDeleting(targetDelete.name)
    try {
      await skillsApi.delete(targetDelete.name)
      await fetchSkills(query)
      setDeleteOpen(false)
      setTargetDelete(null)
      toast.success(t("skills.toast.deleted"))
    } catch (err: unknown) {
      toast.error(extractApiErrorMessage(err, t("skills.toast.deleteFailed")))
    } finally {
      setDeleting(null)
    }
  }

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            placeholder={t("skills.searchPlaceholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-72 rounded-lg bg-card pl-9 text-sm"
          />
        </div>
        <NativeSelect
          value={sourceFilter}
          onChange={(e) => handleSourceFilterChange(e.target.value as "all" | "built_in" | "custom")}
          className="w-36 bg-card"
        >
          <option value="all">{t("skills.filter.allSources")}</option>
          <option value="built_in">{t("shared.term.builtIn")}</option>
          <option value="custom">{t("shared.term.custom")}</option>
        </NativeSelect>
        <NativeSelect
          value={databaseFilter}
          onChange={(e) => handleDatabaseFilterChange(e.target.value)}
          className="w-36 bg-card"
        >
          <option value="all">{t("skills.filter.allScopes")}</option>
          {databaseOptions.map((db) => (
            <option key={db} value={db}>{displaySkillScope(db, t)}</option>
          ))}
        </NativeSelect>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button size="sm" onClick={handleOpenCreate}>
          <Plus className="size-4" />
          {t("skills.btn.create")}
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  const primary = (
    <section className="animate-in fade-in slide-in-from-bottom-1 duration-500">
      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[220px]">{t("skills.col.name")}</TableHead>
              <TableHead>{t("skills.col.description")}</TableHead>
              <TableHead className="w-28">{t("skills.col.source")}</TableHead>
              <TableHead className="w-28">{t("skills.col.scope")}</TableHead>
              <TableHead className="w-20">{t("skills.col.version")}</TableHead>
              <TableHead className="w-20 text-right">{t("skills.col.actions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <ListTableLoadingRows rowCount={6} columnCount={6} />
            ) : error ? (
              <TableRow>
                <TableCell colSpan={6} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <AlertTriangle className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">{error}</p>
                    <Button variant="ghost" size="sm" onClick={() => fetchSkills()}>
                      {t("skills.btn.retry")}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ) : pagedSkills.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <Sparkles className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">
                      {query || sourceFilter !== "all" || databaseFilter !== "all" ? t("skills.empty.noMatch") : t("skills.empty.none")}
                    </p>
                    {query ? (
                      <Button variant="ghost" size="sm" onClick={() => setQuery("")}>
                        {t("skills.btn.clearSearch")}
                      </Button>
                    ) : (
                      <Button variant="ghost" size="sm" onClick={handleOpenCreate}>
                        <Plus className="size-4" />
                        {t("skills.btn.createSkill")}
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              pagedSkills.map((skill, index) => (
                <TableRow
                  key={skill.name}
                  style={{ animationDelay: `${index * 30}ms` }}
                  className={`cursor-pointer transition-colors duration-150 hover:bg-muted/40 ${
                    detailSkill?.name === skill.name ? "bg-primary/[0.04]" : ""
                  }`}
                  onClick={() => setDetailSkill(skill)}
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                        <Sparkles className="size-3.5 text-muted-foreground" />
                      </div>
                      <span className="font-medium text-foreground">{skill.name}</span>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[400px] truncate text-muted-foreground">{skill.description}</TableCell>
                  <TableCell>
                    <Badge variant={skill.source === "built_in" ? "secondary" : "outline"} className="text-[11px]">
                      {displaySkillSource(skill.source, t)}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{displaySkillScope(skill.database, t)}</TableCell>
                  <TableCell className="text-xs tabular-nums text-muted-foreground">{skill.version}</TableCell>
                  <TableCell className="text-right">
                    {skill.source === "custom" ? (
                      <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                        <Button variant="ghost" size="icon-xs" onClick={() => handleOpenEdit(skill)}>
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="text-destructive hover:text-destructive"
                          onClick={() => {
                            setTargetDelete(skill)
                            setDeleteOpen(true)
                          }}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
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
            total={visibleSkills.length}
            onPageChange={setPage}
            className="border-t border-border px-4 py-2"
          />
        ) : null}
      </ListTable>
    </section>
  )

  return (
    <>
      <div className="space-y-6">
        <div className="rounded-xl bg-card p-4 shadow-sm">
          {toolbar}
        </div>
        <div className="rounded-xl bg-card shadow-sm">
          {primary}
        </div>
      </div>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="w-[min(92vw,1200px)] max-w-none p-8 sm:max-w-none" aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>{editingSkillName ? t("skills.dialog.editTitle") : t("skills.dialog.createTitle")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="skill-name" className="text-sm font-medium">{t("skills.form.name")}</label>
                <Input
                  id="skill-name"
                  value={formData.name}
                  disabled={Boolean(editingSkillName)}
                  onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
                  placeholder="ob-slow-query-diagnosis"
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="skill-version" className="text-sm font-medium">{t("skills.form.version")}</label>
                <Input
                  id="skill-version"
                  value={formData.version}
                  onChange={(e) => setFormData((prev) => ({ ...prev, version: e.target.value }))}
                  placeholder="1.0.0"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="skill-description" className="text-sm font-medium">{t("skills.form.description")}</label>
              <Input
                id="skill-description"
                value={formData.description}
                onChange={(e) => setFormData((prev) => ({ ...prev, description: e.target.value }))}
                placeholder={t("skills.form.descPlaceholder")}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="skill-db" className="text-sm font-medium">{t("skills.form.database")}</label>
              <NativeSelect
                id="skill-db"
                value={formData.database}
                onChange={(e) => setFormData((prev) => ({ ...prev, database: e.target.value }))}
              >
                <option value="general">{t("skills.scope.general")}</option>
                {databaseOptions.filter((db) => db !== "general").map((db) => (
                  <option key={db} value={db}>{db}</option>
                ))}
              </NativeSelect>
            </div>
            <div className="flex items-center gap-3 rounded-lg border border-border px-3 py-2">
              <Checkbox
                id="skill-always-apply"
                checked={formData.always_apply}
                onCheckedChange={(checked) => setFormData((prev) => ({ ...prev, always_apply: checked === true }))}
              />
              <label htmlFor="skill-always-apply" className="text-sm text-foreground">{t("skills.form.alwaysApply")}</label>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="skill-prompt" className="text-sm font-medium">{t("skills.form.prompt")}</label>
              <Textarea
                id="skill-prompt"
                value={formData.prompt}
                onChange={(e) => setFormData((prev) => ({ ...prev, prompt: e.target.value }))}
                className="min-h-64 max-h-[50vh] resize-y overflow-y-auto"
                placeholder={t("skills.form.promptPlaceholder")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)} disabled={saving}>{t("skills.btn.cancel")}</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
              {t("skills.btn.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DetailDrawer
        open={Boolean(detailSkill)}
        onOpenChange={(open) => { if (!open) setDetailSkill(null) }}
        title={detailSkill?.name || t("skills.detail.title")}
        description={detailSkill ? `${displaySkillSource(detailSkill.source, t)} / ${displaySkillScope(detailSkill.database, t)}` : ""}
      >
        {detailSkill ? (
          <div className="space-y-4 p-1">
            <div className="rounded-lg border border-border p-3">
              <div className="mb-1 text-xs text-muted-foreground">{t("skills.detail.description")}</div>
              <p className="text-sm text-foreground">{detailSkill.description}</p>
            </div>
            <div className="rounded-lg border border-border p-3">
              <div className="mb-1 text-xs text-muted-foreground">{t("skills.detail.prompt")}</div>
              <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-words text-sm text-foreground">
                {detailSkill.prompt}
              </pre>
            </div>
          </div>
        ) : null}
      </DetailDrawer>

      <ConfirmActionDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t("skills.delete.title")}
        description={t("skills.delete.desc").replace("{name}", targetDelete?.name ?? "")}
        confirmText={t("skills.delete.confirm")}
        confirming={Boolean(deleting)}
        confirmDisabled={!targetDelete}
        onConfirm={handleDelete}
      />
    </>
  )
}

function displaySkillSource(source: Skill["source"], t: (key: "shared.term.builtIn" | "shared.term.custom") => string): string {
  return source === "built_in" ? t("shared.term.builtIn") : t("shared.term.custom")
}

function displaySkillScope(database: Skill["database"], t: (key: "skills.scope.general") => string): string {
  return database === "general" ? t("skills.scope.general") : database
}

function extractApiErrorMessage(error: unknown, fallback: string): string {
  const detail =
    typeof error === "object" && error !== null
      ? (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
      : undefined
  if (typeof detail === "string" && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]
    if (typeof first === "string") return first
    if (first && typeof first.msg === "string") return first.msg
  }
  return fallback
}
