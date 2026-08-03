import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { BookOpen, Download, ExternalLink, Loader2, Package, Pencil, Plus, RefreshCw, Search, Trash2, User } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { useShellI18n } from "@/i18n/shellI18n"
import { knowledgeApi, knowledgePackApi } from "@/lib/api"
import type { KnowledgeBase, KnowledgeBaseInput, KnowledgePack } from "@/lib/api"

const POLL_INTERVAL = 3000

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object") {
    const axiosDetail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
    if (typeof axiosDetail === "string" && axiosDetail.trim()) return axiosDetail
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

type CardItem =
  | { kind: "kb"; kb: KnowledgeBase }
  | { kind: "pack"; pack: KnowledgePack }

export function KnowledgeListPage() {
  const navigate = useNavigate()
  const { t } = useShellI18n()

  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [searchQuery, setSearchQuery] = useState("")

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<KBFormState>({ ...emptyForm })
  const [saving, setSaving] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<KnowledgeBase | null>(null)
  const [deleting, setDeleting] = useState(false)

  const [packs, setPacks] = useState<KnowledgePack[]>([])
  const [packsLoading, setPacksLoading] = useState(false)
  const [installingPacks, setInstallingPacks] = useState<Set<string>>(new Set())
  const [uninstallTarget, setUninstallTarget] = useState<{ id: string; name: string } | null>(null)
  const [uninstalling, setUninstalling] = useState(false)
  const [switchingVersion, setSwitchingVersion] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

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

  const fetchPacks = useCallback(async () => {
    setPacksLoading(true)
    try {
      const list = await knowledgePackApi.list()
      setPacks(Array.isArray(list) ? list : [])
    } catch {
      // packs failing shouldn't block the page
    } finally {
      setPacksLoading(false)
    }
  }, [])

  function fetchAll() {
    fetchData()
    fetchPacks()
  }

  useEffect(() => { fetchAll() }, [])

  // Poll while any pack is downloading
  useEffect(() => {
    const hasDownloading = packs.some((p) => p.status === "downloading")
    if (hasDownloading) {
      pollRef.current = setInterval(async () => {
        try {
          const list = await knowledgePackApi.list()
          setPacks(list)
          if (!list.some((p) => p.status === "downloading")) {
            if (pollRef.current) clearInterval(pollRef.current)
            pollRef.current = null
            fetchData()
          }
        } catch {
          // ignore poll errors
        }
      }, POLL_INTERVAL)
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [packs])

  // Build unified card list: installed KBs + available (not-yet-installed) packs
  const cards = useMemo<CardItem[]>(() => {
    const installedPackIds = new Set(
      knowledgeBases.filter((kb) => kb.source === "pack" && kb.pack_id).map((kb) => kb.pack_id),
    )
    const kbCards: CardItem[] = knowledgeBases.map((kb) => ({ kind: "kb", kb }))
    const availablePackCards: CardItem[] = packs
      .filter((p) => !installedPackIds.has(p.id))
      .map((p) => ({ kind: "pack", pack: p }))
    return [...kbCards, ...availablePackCards]
  }, [knowledgeBases, packs])

  const filtered = useMemo(() => {
    if (!searchQuery) return cards
    const q = searchQuery.toLowerCase()
    return cards.filter((item) => {
      if (item.kind === "kb") {
        const kb = item.kb
        return (
          kb.name.toLowerCase().includes(q) ||
          (kb.description || "").toLowerCase().includes(q) ||
          (kb.tags || []).some((tag) => tag.toLowerCase().includes(q))
        )
      }
      const pack = item.pack
      return (
        pack.name.toLowerCase().includes(q) ||
        pack.description.toLowerCase().includes(q) ||
        pack.tags.some((tag) => tag.toLowerCase().includes(q))
      )
    })
  }, [cards, searchQuery])

  function openCreateDialog() {
    setEditingId(null)
    setForm({ ...emptyForm })
    setDialogOpen(true)
  }

  function openEditDialog(kb: KnowledgeBase) {
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

  async function handleInstallPack(pack: KnowledgePack) {
    setInstallingPacks((prev) => new Set(prev).add(pack.id))
    try {
      await knowledgePackApi.install(pack.id)
      toast.success(t("knowledge.pack.toast.installStarted"))
      fetchPacks()
    } catch (e) {
      toast.error(getErrorMessage(e, t("knowledge.pack.toast.installFailed")))
      setInstallingPacks((prev) => {
        const next = new Set(prev)
        next.delete(pack.id)
        return next
      })
    }
  }

  async function handleUninstallPack() {
    if (!uninstallTarget) return
    setUninstalling(true)
    try {
      await knowledgePackApi.uninstall(uninstallTarget.id)
      toast.success(t("knowledge.pack.toast.uninstalled"))
      setUninstallTarget(null)
      fetchPacks()
      fetchData()
    } catch (e) {
      toast.error(getErrorMessage(e, t("knowledge.pack.toast.uninstallFailed")))
    } finally {
      setUninstalling(false)
    }
  }

  async function handleSwitchVersion(packId: string, version: string) {
    setSwitchingVersion(packId)
    try {
      await knowledgePackApi.switchVersion(packId, version)
      toast.success(t("knowledge.pack.toast.versionSwitched"))
      fetchPacks()
      fetchData()
    } catch (e) {
      toast.error(getErrorMessage(e, t("knowledge.pack.toast.switchFailed")))
    } finally {
      setSwitchingVersion(null)
    }
  }

  // --- Card renderers ---

  function renderKBCard(kb: KnowledgeBase) {
    const isPack = kb.source === "pack"
    const packInfo = isPack ? packs.find((p) => p.id === kb.pack_id) : null
    const hasVersions = packInfo?.versions && packInfo.versions.length > 1
    const isSwitching = switchingVersion === kb.pack_id
    return (
      <Card
        key={`kb-${kb.id}`}
        className="flex flex-col cursor-pointer transition-colors hover:border-primary/30"
        onClick={() => navigate(`/knowledge/${kb.id}`)}
      >
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="text-base line-clamp-1">{kb.name}</CardTitle>
            {hasVersions ? (
              <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
                <Select
                  value={packInfo!.current_version || packInfo!.default_version || ""}
                  onValueChange={(v) => handleSwitchVersion(kb.pack_id!, v)}
                  disabled={isSwitching}
                >
                  <SelectTrigger className="h-6 w-auto gap-0.5 rounded-full border px-2 text-xs font-medium shadow-none">
                    {isSwitching ? <Loader2 className="size-3 animate-spin" /> : <>v<SelectValue /></>}
                  </SelectTrigger>
                  <SelectContent>
                    {packInfo!.versions!.map((v) => (
                      <SelectItem key={v.label} value={v.label} className="text-xs">{v.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : !isPack ? (
              <Badge variant="secondary" className="text-xs shrink-0">
                <User className="mr-1 size-3" />{t("knowledge.source.user")}
              </Badge>
            ) : null}
          </div>
          <CardDescription className="line-clamp-2">
            {kb.description || " "}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex-1">
          <div className="flex flex-wrap gap-1.5 mb-3">
            {(kb.tags || []).map((tag) => (
              <Badge key={tag} variant="secondary" className="text-xs">{tag}</Badge>
            ))}
          </div>
          <div className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground tabular-nums">{kb.document_count}</span>{" "}
            {t("knowledge.docCountUnit")}
          </div>
        </CardContent>
        <CardFooter className="justify-end border-t pt-4" onClick={(e) => e.stopPropagation()}>
          {isPack ? (
            <Button
              size="sm"
              variant="outline"
              className="text-destructive hover:text-destructive"
              onClick={() => {
                if (kb.pack_id) setUninstallTarget({ id: kb.pack_id, name: kb.name })
              }}
            >
              <Trash2 className="size-3.5" />
              {t("knowledge.pack.uninstall")}
            </Button>
          ) : (
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => openEditDialog(kb)}>
                <Pencil className="size-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                onClick={() => setDeleteTarget(kb)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          )}
        </CardFooter>
      </Card>
    )
  }

  function renderPackCard(pack: KnowledgePack) {
    const isInstalling = installingPacks.has(pack.id) || pack.status === "downloading"

    function renderButton() {
      if (isInstalling) {
        return (
          <Button size="sm" variant="outline" disabled>
            <Loader2 className="size-3.5 animate-spin" />
            {t("knowledge.pack.downloading")}
          </Button>
        )
      }
      if (pack.status === "error") {
        return (
          <Button size="sm" variant="outline" onClick={() => handleInstallPack(pack)}>
            <RefreshCw className="size-3.5" />
            {t("knowledge.pack.retry")}
          </Button>
        )
      }
      return (
        <Button size="sm" onClick={() => handleInstallPack(pack)}>
          <Download className="size-3.5" />
          {t("knowledge.pack.install")}
        </Button>
      )
    }

    return (
      <Card key={`pack-${pack.id}`} className="flex flex-col border-dashed">
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="text-base line-clamp-1">{pack.name}</CardTitle>
            {pack.status === "error" ? (
              <Badge variant="destructive" className="text-xs shrink-0">{t("knowledge.pack.error")}</Badge>
            ) : isInstalling ? (
              <Badge variant="secondary" className="text-xs shrink-0">{t("knowledge.pack.downloading")}</Badge>
            ) : pack.current_version ? (
              <Badge variant="outline" className="text-xs shrink-0">v{pack.current_version}</Badge>
            ) : (
              <Badge variant="outline" className="text-xs shrink-0">{t("knowledge.pack.available")}</Badge>
            )}
          </div>
          <CardDescription className="line-clamp-2">{pack.description}</CardDescription>
        </CardHeader>
        <CardContent className="flex-1">
          <div className="flex flex-wrap gap-1.5 mb-3">
            {pack.tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="text-xs">{tag}</Badge>
            ))}
          </div>
          <div className="space-y-1 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <span>{t("knowledge.pack.license")}</span>
              <span className="font-medium text-foreground">{pack.license || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>{t("knowledge.docCountUnit")}</span>
              <span className="font-medium text-foreground tabular-nums">~{pack.estimated_doc_count}</span>
            </div>
            {pack.source_url && (
              <div className="flex justify-between">
                <span>{t("knowledge.pack.source")}</span>
                <a
                  href={pack.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
                  onClick={(e) => e.stopPropagation()}
                >
                  {new URL(pack.source_url).hostname.replace("www.", "")}
                  <ExternalLink className="size-3" />
                </a>
              </div>
            )}
          </div>
          {pack.status === "error" && pack.error_message && (
            <p className="mt-2 text-xs text-destructive line-clamp-2">{pack.error_message}</p>
          )}
        </CardContent>
        <CardFooter className="justify-end border-t pt-4">
          {renderButton()}
        </CardFooter>
      </Card>
    )
  }

  // --- Layout ---

  const isLoading = loading || packsLoading

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
        <Button variant="outline" size="sm" onClick={fetchAll} disabled={isLoading}>
          {isLoading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {t("knowledge.btn.refresh")}
        </Button>
        <Button size="sm" onClick={openCreateDialog}>
          <Plus className="size-4" />
          {t("knowledge.btn.create")}
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  function renderContent() {
    if (loading && knowledgeBases.length === 0) {
      return (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (error) {
      return (
        <div className="flex flex-col items-center gap-2 py-20 text-destructive">
          <BookOpen className="size-5" />
          <span className="text-sm">{error}</span>
        </div>
      )
    }

    if (filtered.length === 0) {
      return (
        <div className="flex flex-col items-center gap-2 py-20 text-muted-foreground">
          <BookOpen className="size-5" />
          <span className="text-sm">{searchQuery ? t("knowledge.empty.noMatch") : t("knowledge.empty.none")}</span>
        </div>
      )
    }

    return (
      <div className="grid gap-4 p-4 md:grid-cols-2 xl:grid-cols-3">
        {filtered.map((item) =>
          item.kind === "kb" ? renderKBCard(item.kb) : renderPackCard(item.pack),
        )}
      </div>
    )
  }

  const primary = (
    <div className="rounded-xl bg-card shadow-sm">
      <div className="flex items-center gap-4 px-4 pt-4">
        <span className="text-xs tabular-nums text-muted-foreground">
          {filtered.length} {t("knowledge.resultCount")}
        </span>
      </div>
      {renderContent()}
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

      <ConfirmActionDialog
        open={!!uninstallTarget}
        title={t("knowledge.pack.uninstall.title")}
        description={t("knowledge.pack.uninstall.desc").replace("{name}", uninstallTarget?.name ?? "")}
        onOpenChange={(open) => !open && setUninstallTarget(null)}
        onConfirm={handleUninstallPack}
        confirming={uninstalling}
      />
    </>
  )
}
