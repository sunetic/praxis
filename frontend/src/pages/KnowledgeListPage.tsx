import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { BookOpen, Loader2, Pencil, Plus, RefreshCw, Search, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { useShellI18n } from "@/i18n/shellI18n"
import { knowledgeApi } from "@/lib/api"
import type { KnowledgeBase, KnowledgeBaseInput } from "@/lib/api"

const PAGE_SIZE = 10

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message
  }
  return fallback
}

type KBFormState = {
  name: string
  description: string
  tags: string
}

const emptyForm: KBFormState = { name: "", description: "", tags: "" }

export function KnowledgeListPage() {
  const navigate = useNavigate()
  const { t } = useShellI18n()
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [searchQuery, setSearchQuery] = useState("")
  const [page, setPage] = useState(1)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<KBFormState>({ ...emptyForm })
  const [saving, setSaving] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<KnowledgeBase | null>(null)
  const [deleting, setDeleting] = useState(false)

  async function fetchData() {
    setLoading(true)
    setError(null)
    try {
      const list = await knowledgeApi.list()
      setKnowledgeBases(list)
    } catch (e) {
      setError(getErrorMessage(e, t("knowledge.loadFailed")))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchData() }, [])

  const filtered = useMemo(() => {
    if (!searchQuery) return knowledgeBases
    const q = searchQuery.toLowerCase()
    return knowledgeBases.filter(
      (kb) =>
        kb.name.toLowerCase().includes(q) ||
        (kb.description || "").toLowerCase().includes(q) ||
        (kb.tags || []).some((tag) => tag.toLowerCase().includes(q)),
    )
  }, [knowledgeBases, searchQuery])

  const paged = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filtered.slice(start, start + PAGE_SIZE)
  }, [filtered, page])

  useEffect(() => setPage(1), [searchQuery])

  function openCreateDialog() {
    setEditingId(null)
    setForm({ ...emptyForm })
    setDialogOpen(true)
  }

  function openEditDialog(kb: KnowledgeBase, e?: React.MouseEvent) {
    if (e) e.stopPropagation()
    setEditingId(kb.id)
    setForm({
      name: kb.name,
      description: kb.description || "",
      tags: (kb.tags || []).join(", "),
    })
    setDialogOpen(true)
  }

  async function handleSave() {
    setSaving(true)
    try {
      const payload: KnowledgeBaseInput = {
        name: form.name,
        description: form.description || undefined,
        tags: form.tags
          ? form.tags.split(",").map((tag) => tag.trim()).filter(Boolean)
          : undefined,
      }
      if (editingId) {
        await knowledgeApi.update(editingId, payload)
        toast.success(t("knowledge.toast.updated"))
      } else {
        await knowledgeApi.create(payload)
        toast.success(t("knowledge.toast.created"))
      }
      setDialogOpen(false)
      fetchData()
    } catch (e) {
      toast.error(getErrorMessage(e, t("knowledge.toast.saveFailed")))
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await knowledgeApi.delete(deleteTarget.id)
      toast.success(t("knowledge.toast.deleted"))
      setDeleteTarget(null)
      fetchData()
    } catch (e) {
      toast.error(getErrorMessage(e, t("knowledge.toast.deleteFailed")))
    } finally {
      setDeleting(false)
    }
  }

  const columnCount = 5

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            placeholder={t("knowledge.searchPlaceholder")}
            className="w-72 rounded-lg bg-card pl-9 text-sm"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {t("knowledge.btn.refresh")}
        </Button>
        <Button size="sm" onClick={openCreateDialog}>
          <Plus className="size-4" />
          {t("knowledge.btn.create")}
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  function renderTableBody() {
    if (loading) {
      return <ListTableLoadingRows rowCount={5} columnCount={columnCount} />
    }

    if (error) {
      return (
        <TableRow>
          <TableCell colSpan={columnCount} className="h-32 text-center">
            <div className="flex flex-col items-center gap-2 text-destructive">
              <BookOpen className="size-5" />
              <span className="text-sm">{error}</span>
            </div>
          </TableCell>
        </TableRow>
      )
    }

    if (filtered.length === 0) {
      return (
        <TableRow>
          <TableCell colSpan={columnCount} className="h-32 text-center">
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <BookOpen className="size-5" />
              <span className="text-sm">{searchQuery ? t("knowledge.empty.noMatch") : t("knowledge.empty.none")}</span>
            </div>
          </TableCell>
        </TableRow>
      )
    }

    return paged.map((kb, idx) => (
      <TableRow
        key={kb.id}
        className="animate-in fade-in slide-in-from-bottom-1 cursor-pointer transition-colors duration-150 hover:bg-muted/40 duration-500"
        style={{ animationDelay: `${idx * 30}ms` }}
        onClick={() => navigate(`/knowledge/${kb.id}`)}
      >
        <TableCell className="font-mono text-xs text-muted-foreground">
          {kb.id}
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-2.5">
            <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
              <BookOpen className="size-3.5 text-muted-foreground" />
            </div>
            <div>
              <span className="font-medium">{kb.name}</span>
              {kb.description && (
                <p className="text-xs text-muted-foreground line-clamp-1">{kb.description}</p>
              )}
            </div>
          </div>
        </TableCell>
        <TableCell>
          <div className="flex flex-wrap gap-1">
            {(kb.tags || []).map((tag) => (
              <Badge key={tag} variant="secondary" className="text-xs">
                {tag}
              </Badge>
            ))}
          </div>
        </TableCell>
        <TableCell className="text-muted-foreground tabular-nums">
          {kb.document_count} {t("knowledge.docCountUnit")}
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-0.5" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={(e) => openEditDialog(kb, e)}
            >
              <Pencil className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-destructive hover:text-destructive"
              onClick={() => setDeleteTarget(kb)}
            >
              <Trash2 className="size-3.5" />
            </Button>
          </div>
        </TableCell>
      </TableRow>
    ))
  }

  const primary = (
    <div className="rounded-xl bg-card shadow-sm">
      <div className="flex items-center gap-4 px-4 pt-4">
        <Tabs value="all" onValueChange={() => {}} className="w-fit">
          <TabsList>
            <TabsTrigger value="all">{t("knowledge.tab.all")}</TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="text-xs tabular-nums text-muted-foreground">
          {filtered.length} {t("knowledge.resultCount")}
        </span>
      </div>
      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>{t("knowledge.col.id")}</TableHead>
              <TableHead>{t("knowledge.col.name")}</TableHead>
              <TableHead>{t("knowledge.col.tags")}</TableHead>
              <TableHead>{t("knowledge.col.docCount")}</TableHead>
              <TableHead className="text-right">{t("knowledge.col.actions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>{renderTableBody()}</TableBody>
        </Table>
        <PaginationFooter
          page={page}
          pageSize={PAGE_SIZE}
          total={filtered.length}
          onPageChange={setPage}
        />
      </ListTable>
    </div>
  )

  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId ? t("knowledge.dialog.editTitle") : t("knowledge.dialog.createTitle")}</DialogTitle>
            <DialogDescription>
              {editingId ? t("knowledge.dialog.editDesc") : t("knowledge.dialog.createDesc")}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm text-muted-foreground">{t("knowledge.form.name")}</label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t("knowledge.form.namePlaceholder")}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted-foreground">{t("knowledge.form.description")}</label>
              <Input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder={t("knowledge.form.descPlaceholder")}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted-foreground">{t("knowledge.form.tags")}</label>
              <Input
                value={form.tags}
                onChange={(e) => setForm({ ...form, tags: e.target.value })}
                placeholder={t("knowledge.form.tagsPlaceholder")}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {t("knowledge.btn.cancel")}
            </Button>
            <Button onClick={handleSave} disabled={saving || !form.name}>
              {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
              {editingId ? t("knowledge.btn.save") : t("knowledge.btn.createSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmActionDialog
        open={!!deleteTarget}
        title={t("knowledge.delete.title")}
        description={t("knowledge.delete.desc").replace("{name}", deleteTarget?.name ?? "")}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        onConfirm={handleDelete}
        confirming={deleting}
      />
    </>
  )
}
