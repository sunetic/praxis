import { useEffect, useMemo, useState } from "react"
import { ChevronDown, ChevronUp, Database, Loader2, Pencil, Plus, RefreshCw, Search, Trash2, Zap } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { useShellI18n } from "@/i18n/shellI18n"
import { datasourcesApi } from "@/lib/api"
import type { DataSource, DataSourceInput, DataSourceUpdateInput } from "@/lib/api"

// ─── Constants ───────────────────────────────────────────────────────────────

const PAGE_SIZE = 10
const ALL_CLUSTERS = "__all__"

const ATTRIBUTE_FIELDS: { key: string; label: string; placeholder: string }[] = []

// ─── Helpers ─────────────────────────────────────────────────────────────────

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message
  }
  return fallback
}

function toStringRecord(attrs: Record<string, unknown> | null | undefined): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v != null && v !== "") result[k] = String(v)
  }
  return result
}

function generateAutoClusterKey(seed?: string) {
  const normalized = (seed || "cluster")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 24)
  const prefix = normalized || "cluster"
  const suffix = Math.random().toString(36).slice(2, 6)
  return `${prefix}-${suffix}`
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export function DataSourcesPage() {
  const { t } = useShellI18n()

  // ── Data state ─────────────────────────────────────────────────────────────
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // ── Filter state ───────────────────────────────────────────────────────────
  const [search, setSearch] = useState("")
  const [clusterFilter, setClusterFilter] = useState(ALL_CLUSTERS)
  const [page, setPage] = useState(1)

  // ── Dialog / action state ──────────────────────────────────────────────────
  const [showAddModal, setShowAddModal] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [rowVerifyingId, setRowVerifyingId] = useState<number | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<{ id: number; name: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [clusterKeyCustom, setClusterKeyCustom] = useState(false)

  // ── Form ───────────────────────────────────────────────────────────────────
  const [form, setForm] = useState({
    name: "",
    cluster_key: "",
    host: "",
    port: 3306,
    db_type: "mysql",
    tenant_role: "user" as "sys" | "user",
    user: "",
    password: "",
    database: "",
    attributes: {} as Record<string, string>,
  })
  const [metaOpen, setMetaOpen] = useState(false)

  // ── Fetch ──────────────────────────────────────────────────────────────────
  const fetchDatasources = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await datasourcesApi.list()
      setDatasources(data)
    } catch (err) {
      console.error("Failed to fetch datasources:", err)
      setError(getErrorMessage(err, t("ds.loadFailed")))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchDatasources()
  }, [])

  // ── Derived data ───────────────────────────────────────────────────────────
  const clusterKeyOptions = useMemo(
    () =>
      Array.from(
        new Set(
          datasources
            .map((item) => item.cluster_key)
            .filter((item): item is string => Boolean(item && item.trim()))
        )
      ).sort((a, b) => a.localeCompare(b)),
    [datasources]
  )

  const filteredDatasources = useMemo(() => {
    let items = datasources
    if (clusterFilter !== ALL_CLUSTERS) {
      items = items.filter((ds) => ds.cluster_key === clusterFilter)
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      items = items.filter(
        (ds) =>
          ds.name.toLowerCase().includes(q) ||
          ds.host.toLowerCase().includes(q) ||
          ds.cluster_key.toLowerCase().includes(q)
      )
    }
    return items
  }, [datasources, clusterFilter, search])

  const pagedDatasources = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filteredDatasources.slice(start, start + PAGE_SIZE)
  }, [filteredDatasources, page])

  // ── Reset page on filter change ────────────────────────────────────────────
  useEffect(() => {
    setPage(1)
  }, [search, clusterFilter])

  // ── Handlers ───────────────────────────────────────────────────────────────
  const handleVerifyConnection = async () => {
    if (!form.host || !form.user || !form.password) {
      toast.error(t("ds.verifyRequiresFields"))
      return
    }
    setVerifying(true)
    try {
      const result = await datasourcesApi.test(form)
      if (result.success) {
        toast.success(t("ds.connectSuccess"))
      } else {
        toast.error(result.message)
      }
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, t("ds.connectFailed")))
    } finally {
      setVerifying(false)
    }
  }

  const resetForm = () => {
    setForm({
      name: "",
      cluster_key: "",
      host: "",
      port: 3306,
      db_type: "mysql",
      tenant_role: "user",
      user: "",
      password: "",
      database: "",
      attributes: {},
    })
    setMetaOpen(false)
    setClusterKeyCustom(false)
  }

  const handleAdd = async () => {
    if (!form.name || !form.cluster_key.trim() || !form.host || !form.user) {
      toast.error(t("ds.requiredFields"))
      return
    }
    if (!editingId && !form.password) {
      toast.error(t("ds.passwordRequired"))
      return
    }

    setSaving(true)
    try {
      if (editingId) {
        const cleanAttrs = Object.fromEntries(Object.entries(form.attributes).filter(([, v]) => v !== ""))
        const updatePayload: DataSourceUpdateInput = {
          name: form.name,
          cluster_key: form.cluster_key.trim(),
          host: form.host,
          port: form.port,
          db_type: form.db_type,
          tenant_role: form.tenant_role,
          user: form.user,
          database: form.database,
          attributes: Object.keys(cleanAttrs).length > 0 ? cleanAttrs : null,
        }
        if (form.password.trim()) {
          updatePayload.password = form.password
        }
        await datasourcesApi.update(editingId, updatePayload)
        toast.success(t("ds.updated"))
      } else {
        const cleanAttrs = Object.fromEntries(Object.entries(form.attributes).filter(([, v]) => v !== ""))
        const createPayload: DataSourceInput = {
          name: form.name,
          cluster_key: form.cluster_key.trim(),
          host: form.host,
          port: form.port,
          db_type: form.db_type,
          tenant_role: form.tenant_role,
          user: form.user,
          password: form.password,
          database: form.database,
          attributes: Object.keys(cleanAttrs).length > 0 ? cleanAttrs : null,
        }
        await datasourcesApi.create(createPayload)
        toast.success(t("ds.created"))
      }
      setShowAddModal(false)
      setEditingId(null)
      resetForm()
      fetchDatasources()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, t("ds.saveFailed")))
      console.error("Failed to save datasource:", err)
    } finally {
      setSaving(false)
    }
  }

  const handleEdit = (ds: DataSource) => {
    setEditingId(ds.id)
    setForm({
      name: ds.name,
      cluster_key: ds.cluster_key || `${ds.host}:${ds.port}`,
      host: ds.host,
      port: ds.port,
      db_type: ds.db_type,
      tenant_role: ds.tenant_role,
      user: ds.user || "",
      password: "",
      database: ds.database || "",
      attributes: toStringRecord(ds.attributes),
    })
    const hasAttrs = ds.attributes && ATTRIBUTE_FIELDS.some((f) => ds.attributes?.[f.key] != null)
    setMetaOpen(Boolean(hasAttrs))
    setClusterKeyCustom(true)
    setShowAddModal(true)
  }

  const handleDelete = async (id: number) => {
    setDeleting(true)
    try {
      await datasourcesApi.delete(id)
      toast.success(t("ds.deleted"))
      setDeleteConfirm(null)
      fetchDatasources()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, t("ds.deleteFailed")))
      console.error("Failed to delete datasource:", err)
    } finally {
      setDeleting(false)
    }
  }

  const openCreateDialog = () => {
    setEditingId(null)
    const initialKey = clusterKeyOptions.length > 0 ? clusterKeyOptions[0] : generateAutoClusterKey()
    resetForm()
    setForm((f) => ({ ...f, cluster_key: initialKey }))
    setShowAddModal(true)
  }

  // ── Toolbar ────────────────────────────────────────────────────────────────
  const toolbar = (
    <div className="rounded-xl bg-card p-4 shadow-sm">
      <FilterToolbar>
        <FilterToolbarGroup>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
            <Input
              placeholder={t("ds.searchPlaceholder")}
              className="w-72 rounded-lg bg-card pl-9 text-sm"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {clusterKeyOptions.length > 1 && (
            <Select value={clusterFilter} onValueChange={setClusterFilter}>
              <SelectTrigger className="w-44 bg-card">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_CLUSTERS}>{t("ds.allClusters")}</SelectItem>
                {clusterKeyOptions.map((key) => (
                  <SelectItem key={key} value={key}>
                    {key}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </FilterToolbarGroup>
        <FilterToolbarGroup>
          <Button variant="outline" size="sm" onClick={fetchDatasources} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            {t("ds.refresh")}
          </Button>
          <Button size="sm" onClick={openCreateDialog}>
            <Plus className="size-4" />
            {t("ds.create")}
          </Button>
        </FilterToolbarGroup>
      </FilterToolbar>
    </div>
  )

  // ── Table columns ──────────────────────────────────────────────────────────
  const columnCount = 6

  function renderTableBody() {
    if (loading) {
      return <ListTableLoadingRows rowCount={6} columnCount={columnCount} />
    }

    if (error) {
      return (
        <TableRow>
          <TableCell colSpan={columnCount} className="h-32 text-center">
            <div className="flex flex-col items-center gap-2 text-destructive">
              <Database className="size-5" />
              <span className="text-sm">{error}</span>
            </div>
          </TableCell>
        </TableRow>
      )
    }

    if (filteredDatasources.length === 0) {
      return (
        <TableRow>
          <TableCell colSpan={columnCount} className="h-32 text-center">
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Database className="size-5" />
              <span className="text-sm">{search || clusterFilter !== ALL_CLUSTERS ? t("ds.emptyNoMatch") : t("ds.emptyNone")}</span>
            </div>
          </TableCell>
        </TableRow>
      )
    }

    return pagedDatasources.map((ds, idx) => (
      <TableRow
        key={ds.id}
        className="animate-in fade-in slide-in-from-bottom-1 cursor-pointer transition-colors duration-150 hover:bg-muted/40 duration-500"
        style={{ animationDelay: `${idx * 30}ms` }}
        onClick={() => handleEdit(ds)}
      >
        <TableCell className="font-mono text-xs text-muted-foreground">
          {ds.id}
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-2.5">
            <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
              <Database className="size-3.5 text-muted-foreground" />
            </div>
            <span className="font-medium">{ds.name}</span>
          </div>
        </TableCell>
        <TableCell>
          <Badge variant="outline" className="border-border text-muted-foreground">
            {ds.cluster_key}
          </Badge>
        </TableCell>
        <TableCell className="text-muted-foreground">
          {ds.host}:{ds.port}
        </TableCell>
        <TableCell>
          <Badge variant="outline" className="border-border">
            {ds.db_type === "oceanbase" ? "OceanBase" : ds.db_type === "mysql" ? "MySQL" : ds.db_type}
          </Badge>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-0.5" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("ds.editAria")}
              onClick={() => handleEdit(ds)}
            >
              <Pencil className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("ds.verifyAria")}
              onClick={async () => {
                setRowVerifyingId(ds.id)
                try {
                  const result = await datasourcesApi.testById(ds.id)
                  if (result.success) {
                    toast.success(t("ds.connectSuccess"))
                  } else {
                    toast.error(result.message)
                  }
                } catch (err: unknown) {
                  toast.error(getErrorMessage(err, t("ds.connectFailed")))
                } finally {
                  setRowVerifyingId(null)
                }
              }}
              disabled={rowVerifyingId === ds.id}
            >
              {rowVerifyingId === ds.id ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Zap className="size-3.5" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("ds.deleteAria")}
              className="text-destructive hover:text-destructive"
              onClick={() => setDeleteConfirm({ id: ds.id, name: ds.name })}
            >
              <Trash2 className="size-3.5" />
            </Button>
          </div>
        </TableCell>
      </TableRow>
    ))
  }

  // ── Primary content ────────────────────────────────────────────────────────
  const primary = (
    <div className="rounded-xl bg-card shadow-sm">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <Tabs value="all" onValueChange={() => {}} className="w-fit">
          <TabsList>
            <TabsTrigger value="all">{t("ds.tabAll")}</TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="text-xs tabular-nums text-muted-foreground">
          {filteredDatasources.length} {t("ds.resultCount")}
        </span>
      </div>
      <ListTable className="overflow-hidden border-0 rounded-none shadow-none">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>ID</TableHead>
              <TableHead>{t("ds.col.name")}</TableHead>
              <TableHead>{t("ds.col.cluster")}</TableHead>
              <TableHead>{t("ds.col.host")}</TableHead>
              <TableHead>{t("ds.col.type")}</TableHead>
              <TableHead className="text-right">{t("ds.col.actions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>{renderTableBody()}</TableBody>
        </Table>
        <PaginationFooter
          page={page}
          pageSize={PAGE_SIZE}
          total={filteredDatasources.length}
          onPageChange={setPage}
        />
      </ListTable>
    </div>
  )

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />

      {/* Delete confirmation */}
      <ConfirmActionDialog
        open={!!deleteConfirm}
        onOpenChange={(open) => {
          if (!open) setDeleteConfirm(null)
        }}
        title={t("ds.deleteTitle")}
        description={
          <>
            {t("ds.deleteDescPre")} <span className="font-semibold text-foreground">{deleteConfirm?.name}</span> {t("ds.deleteDescPost")}
          </>
        }
        confirmText={t("ds.deleteConfirm")}
        confirming={deleting}
        confirmDisabled={!deleteConfirm}
        onConfirm={() => deleteConfirm && handleDelete(deleteConfirm.id)}
      />

      {/* Create / Edit dialog */}
      <Dialog
        open={showAddModal}
        onOpenChange={(open) => {
          setShowAddModal(open)
          if (!open) setEditingId(null)
        }}
      >
        <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? t("ds.dialogTitleEdit") : t("ds.dialogTitleAdd")}</DialogTitle>
          </DialogHeader>

          <div className="space-y-5">
            {/* ── Basic Info ─────────────────────────────────────── */}
            <div className="space-y-3">
              <p className="text-xs font-medium text-muted-foreground">{t("ds.sectionBasic")}</p>

              <div>
                <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.name")} <span className="text-destructive">*</span></label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="my-database"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.clusterKey")} <span className="text-destructive">*</span></label>
                {clusterKeyOptions.length > 0 && !clusterKeyCustom ? (
                  <Select
                    value={form.cluster_key}
                    onValueChange={(v) => {
                      if (v === "__custom__") {
                        setClusterKeyCustom(true)
                        setForm({ ...form, cluster_key: "" })
                      } else {
                        setForm({ ...form, cluster_key: v })
                      }
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={t("ds.clusterKeyPlaceholder")} />
                    </SelectTrigger>
                    <SelectContent>
                      {clusterKeyOptions.map((key) => (
                        <SelectItem key={key} value={key}>{key}</SelectItem>
                      ))}
                      <SelectItem value="__custom__" className="text-muted-foreground">{t("ds.clusterKeyCustom")}</SelectItem>
                    </SelectContent>
                  </Select>
                ) : (
                  <div className="flex items-center gap-2">
                    <Input
                      value={form.cluster_key}
                      onChange={(e) => setForm({ ...form, cluster_key: e.target.value })}
                      placeholder="cluster-prod-a"
                      className="flex-1"
                      autoFocus={clusterKeyCustom && clusterKeyOptions.length > 0}
                    />
                    {clusterKeyOptions.length > 0 ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="shrink-0"
                        onClick={() => { setClusterKeyCustom(false); setForm({ ...form, cluster_key: clusterKeyOptions[0] }) }}
                      >
                        {t("ds.clusterKeySelectExisting")}
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="shrink-0"
                        onClick={() => setForm({ ...form, cluster_key: generateAutoClusterKey(form.name) })}
                      >
                        {t("ds.clusterKeyGenerate")}
                      </Button>
                    )}
                  </div>
                )}
              </div>

              <div>
                <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.dbType")}</label>
                <Select value={form.db_type} onValueChange={(v) => setForm({ ...form, db_type: v })}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="mysql">MySQL</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* ── Connection Info ─────────────────────────────────────── */}
            <div className="space-y-3">
              <p className="text-xs font-medium text-muted-foreground">{t("ds.sectionConnection")}</p>

              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2">
                  <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.host")} <span className="text-destructive">*</span></label>
                  <Input
                    value={form.host}
                    onChange={(e) => setForm({ ...form, host: e.target.value })}
                    placeholder="192.168.1.1"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.port")}</label>
                  <Input
                    type="number"
                    value={form.port}
                    onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) })}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.user")} <span className="text-destructive">*</span></label>
                  <Input
                    value={form.user}
                    onChange={(e) => setForm({ ...form, user: e.target.value })}
                    placeholder="root@test"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.password")}{editingId ? "" : " *"}</label>
                  <Input
                    type="password"
                    value={form.password}
                    onChange={(e) => setForm({ ...form, password: e.target.value })}
                    placeholder={editingId ? t("ds.passwordPlaceholderEdit") : "******"}
                  />
                </div>
              </div>

              <div>
                <label className="mb-1.5 block text-sm text-foreground">{t("ds.label.database")}</label>
                <Input
                  value={form.database}
                  onChange={(e) => setForm({ ...form, database: e.target.value })}
                  placeholder="app_db"
                />
              </div>
            </div>

            {/* ── Extended Attributes (collapsible) ──────────────────────────────── */}
            <div>
              <button
                type="button"
                className="flex w-full items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setMetaOpen(!metaOpen)}
              >
                {metaOpen ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
                {t("ds.sectionAttributes")}
                {ATTRIBUTE_FIELDS.some((f) => form.attributes[f.key]) && (
                  <span className="ml-1 text-muted-foreground/60">
                    · {ATTRIBUTE_FIELDS.filter((f) => form.attributes[f.key]).length} {t("ds.attrFilled")}
                  </span>
                )}
              </button>
              {metaOpen && (
                <div className="mt-2 space-y-2">
                  {ATTRIBUTE_FIELDS.map((field) => (
                    <div key={field.key} className="flex items-center gap-2">
                      <label className="w-[140px] shrink-0 text-xs text-muted-foreground">{field.label}</label>
                      <Input
                        value={form.attributes[field.key] ?? ""}
                        onChange={(e) => {
                          const next = { ...form.attributes, [field.key]: e.target.value }
                          setForm({ ...form, attributes: next })
                        }}
                        placeholder={field.placeholder}
                        className="flex-1 text-xs"
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <DialogFooter className="mt-2 flex items-center justify-between sm:justify-between">
            <Button
              variant="outline"
              onClick={handleVerifyConnection}
              disabled={verifying || !form.host || !form.user || !form.password}
            >
              {verifying ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Zap className="mr-2 size-4" />}
              {t("ds.verifyConnection")}
            </Button>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setShowAddModal(false)
                  setEditingId(null)
                }}
              >
                {t("ds.cancel")}
              </Button>
              <Button onClick={handleAdd} disabled={saving}>
                {saving ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
                {editingId ? t("ds.update") : t("ds.save")}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
