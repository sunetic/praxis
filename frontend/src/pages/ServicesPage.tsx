import { useEffect, useMemo, useState } from "react"
import { Loader2, Pencil, Plug, Plus, RefreshCw, Search, Trash2, Zap } from "lucide-react"
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { servicesApi, datasourcesApi } from "@/lib/api"
import type { Service, ServiceInput, DataSource } from "@/lib/api"

const PAGE_SIZE = 10

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message
  }
  return fallback
}

type OcpApiConfig = {
  host: string
  port: number
  user: string
  password: string
}

const emptyOcpConfig: OcpApiConfig = { host: "", port: 8080, user: "", password: "" }

const RESOURCE_TYPES = [
  { value: "cluster", label: "集群" },
  { value: "datasource", label: "数据源" },
] as const

type ResourceRefType = (typeof RESOURCE_TYPES)[number]["value"]

function parseResourceRef(ref: string | null | undefined): { type: ResourceRefType; value: string } {
  if (!ref) return { type: "cluster", value: "" }
  const colonIdx = ref.indexOf(":")
  if (colonIdx === -1) return { type: "cluster", value: ref }
  const prefix = ref.slice(0, colonIdx)
  const value = ref.slice(colonIdx + 1)
  if (RESOURCE_TYPES.some((t) => t.value === prefix)) {
    return { type: prefix as ResourceRefType, value }
  }
  return { type: "cluster", value: ref }
}

function buildResourceRef(type: ResourceRefType, value: string): string {
  if (!value.trim()) return ""
  return `${type}:${value.trim()}`
}

function resolveResourceLabel(
  ref: string | null | undefined,
  datasourceById: Map<string, DataSource>,
  clusterKeySet: Set<string>,
): string {
  const { type, value } = parseResourceRef(ref)
  if (!value) return "无"
  if (type === "datasource") {
    return datasourceById.get(value)?.name || "无"
  }
  return clusterKeySet.has(value) ? value : "无"
}

type ServiceFormState = {
  name: string
  service_type: string
  resourceType: ResourceRefType
  resourceValue: string
  config: OcpApiConfig
}

const emptyForm: ServiceFormState = {
  name: "",
  service_type: "ocp_api",
  resourceType: "cluster",
  resourceValue: "",
  config: { ...emptyOcpConfig },
}

export function ServicesPage() {
  const [services, setServices] = useState<Service[]>([])
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [searchQuery, setSearchQuery] = useState("")
  const [page, setPage] = useState(1)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<ServiceFormState>({ ...emptyForm })
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [rowTestingId, setRowTestingId] = useState<number | null>(null)

  const [deleteTarget, setDeleteTarget] = useState<Service | null>(null)
  const [deleting, setDeleting] = useState(false)

  const clusterKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const ds of datasources) {
      if (ds.cluster_key) keys.add(ds.cluster_key)
    }
    return Array.from(keys).sort()
  }, [datasources])

  const datasourceById = useMemo(() => {
    const map = new Map<string, DataSource>()
    for (const ds of datasources) {
      map.set(String(ds.id), ds)
    }
    return map
  }, [datasources])

  const clusterKeySet = useMemo(() => new Set(clusterKeys), [clusterKeys])

  async function fetchData() {
    setLoading(true)
    setError(null)
    try {
      const [svcList, dsList] = await Promise.all([servicesApi.list(), datasourcesApi.list()])
      setServices(svcList)
      setDatasources(dsList)
    } catch (e) {
      setError(getErrorMessage(e, "加载失败"))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchData() }, [])

  const filtered = useMemo(() => {
    if (!searchQuery) return services
    const q = searchQuery.toLowerCase()
    return services.filter((s) => {
      const resourceLabel = resolveResourceLabel(s.resource_ref, datasourceById, clusterKeySet)
      return s.name.toLowerCase().includes(q) || resourceLabel.toLowerCase().includes(q)
    })
  }, [services, searchQuery, datasourceById, clusterKeySet])

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

  function openEditDialog(svc: Service) {
    setEditingId(svc.id)
    const cfg = (svc.config as OcpApiConfig | null) || { ...emptyOcpConfig }
    const parsed = parseResourceRef(svc.resource_ref)
    setForm({
      name: svc.name,
      service_type: svc.service_type,
      resourceType: parsed.type,
      resourceValue: parsed.value,
      config: { host: cfg.host || "", port: cfg.port || 8080, user: cfg.user || "", password: cfg.password || "" },
    })
    setDialogOpen(true)
  }

  function buildPayload(): ServiceInput {
    return {
      name: form.name,
      service_type: form.service_type,
      resource_ref: buildResourceRef(form.resourceType, form.resourceValue) || undefined,
      config: form.config,
    }
  }

  async function handleSave() {
    setSaving(true)
    try {
      const payload = buildPayload()
      if (editingId) {
        await servicesApi.update(editingId, payload)
        toast.success("更新成功")
      } else {
        await servicesApi.create(payload)
        toast.success("创建成功")
      }
      setDialogOpen(false)
      fetchData()
    } catch (e) {
      toast.error(getErrorMessage(e, "保存失败"))
    } finally {
      setSaving(false)
    }
  }

  async function handleTestInDialog() {
    if (!form.config.host) {
      toast.error("测试连接需要填写主机")
      return
    }
    setTesting(true)
    try {
      let result: { success: boolean; message: string }
      if (editingId) {
        result = await servicesApi.test(editingId)
      } else {
        result = await servicesApi.testConfig(buildPayload())
      }
      if (result.success) {
        toast.success("连接成功")
      } else {
        toast.error(result.message || "连接失败")
      }
    } catch (e) {
      toast.error(getErrorMessage(e, "测试连接失败"))
    } finally {
      setTesting(false)
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await servicesApi.delete(deleteTarget.id)
      toast.success("已删除")
      setDeleteTarget(null)
      fetchData()
    } catch (e) {
      toast.error(getErrorMessage(e, "删除失败"))
    } finally {
      setDeleting(false)
    }
  }

  async function handleTestConnection(svc: Service) {
    setRowTestingId(svc.id)
    try {
      const result = await servicesApi.test(svc.id)
      if (result.success) {
        toast.success("连接成功")
      } else {
        toast.error(result.message || "连接失败")
      }
    } catch (e) {
      toast.error(getErrorMessage(e, "测试连接失败"))
    } finally {
      setRowTestingId(null)
    }
  }

  const columnCount = 5

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            placeholder="搜索服务..."
            className="w-72 rounded-lg bg-card pl-9 text-sm"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
        <Button size="sm" onClick={openCreateDialog}>
          <Plus className="size-4" />
          新增服务
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
              <Plug className="size-5" />
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
              <Plug className="size-5" />
              <span className="text-sm">{searchQuery ? "没有匹配的服务" : "暂无服务，请添加"}</span>
            </div>
          </TableCell>
        </TableRow>
      )
    }

    return paged.map((svc, idx) => (
      <TableRow
        key={svc.id}
        className="animate-in fade-in slide-in-from-bottom-1 cursor-pointer transition-colors duration-150 hover:bg-muted/40 duration-500"
        style={{ animationDelay: `${idx * 30}ms` }}
        onClick={() => openEditDialog(svc)}
      >
        <TableCell className="font-mono text-xs text-muted-foreground">
          {svc.id}
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-2.5">
            <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
              <Plug className="size-3.5 text-muted-foreground" />
            </div>
            <span className="font-medium">{svc.name}</span>
          </div>
        </TableCell>
        <TableCell>
          <Badge variant="outline" className="border-border text-muted-foreground">
            {svc.service_type}
          </Badge>
        </TableCell>
        <TableCell className="text-muted-foreground text-sm font-mono">
          {resolveResourceLabel(svc.resource_ref, datasourceById, clusterKeySet)}
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-0.5" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => handleTestConnection(svc)}
              disabled={rowTestingId === svc.id}
            >
              {rowTestingId === svc.id ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Zap className="size-3.5" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => openEditDialog(svc)}
            >
              <Pencil className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-destructive hover:text-destructive"
              onClick={() => setDeleteTarget(svc)}
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
            <TabsTrigger value="all">全部服务</TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="text-xs tabular-nums text-muted-foreground">
          {filtered.length} 项结果
        </span>
      </div>
      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>ID</TableHead>
              <TableHead>名称</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>关联资源</TableHead>
              <TableHead className="text-right">操作</TableHead>
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
        <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? "编辑服务" : "新增服务"}</DialogTitle>
            <DialogDescription>
              {editingId ? "修改服务配置" : "添加新的外部服务连接"}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-sm text-muted-foreground">服务类型</label>
                <Select
                  value={form.service_type}
                  onValueChange={(v) => setForm({ ...form, service_type: v })}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ocp_api">OCP API</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="mb-1 block text-sm text-muted-foreground">名称</label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="例：生产 OCP"
                />
              </div>
            </div>

            <div>
              <label className="mb-1 block text-sm text-muted-foreground">关联资源</label>
              <div className="grid grid-cols-[140px_1fr] gap-2">
                <Select
                  value={form.resourceType}
                  onValueChange={(v) => setForm({ ...form, resourceType: v as ResourceRefType, resourceValue: "" })}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RESOURCE_TYPES.map((rt) => (
                      <SelectItem key={rt.value} value={rt.value}>{rt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {form.resourceType === "cluster" ? (
                  clusterKeys.length > 0 ? (
                    <Select
                      value={form.resourceValue || "__none__"}
                      onValueChange={(v) => setForm({ ...form, resourceValue: v === "__none__" ? "" : v })}
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="选择集群（可选）" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__none__">无关联</SelectItem>
                        {clusterKeys.map((ck) => (
                          <SelectItem key={ck} value={ck}>{ck}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      value={form.resourceValue}
                      onChange={(e) => setForm({ ...form, resourceValue: e.target.value })}
                      placeholder="集群标识"
                    />
                  )
                ) : (
                  datasources.length > 0 ? (
                    <Select
                      value={form.resourceValue || "__none__"}
                      onValueChange={(v) => setForm({ ...form, resourceValue: v === "__none__" ? "" : v })}
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="选择数据源（可选）" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__none__">无关联</SelectItem>
                        {datasources.map((ds) => (
                          <SelectItem key={ds.id} value={String(ds.id)}>{ds.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      value={form.resourceValue}
                      onChange={(e) => setForm({ ...form, resourceValue: e.target.value })}
                      placeholder="数据源 ID"
                    />
                  )
                )}
              </div>
            </div>

            <div className="border-t pt-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1 block text-sm text-muted-foreground">主机</label>
                  <Input
                    value={form.config.host}
                    onChange={(e) =>
                      setForm({ ...form, config: { ...form.config, host: e.target.value } })
                    }
                    placeholder="6.12.233.34"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm text-muted-foreground">端口</label>
                  <Input
                    type="number"
                    value={form.config.port}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        config: { ...form.config, port: parseInt(e.target.value) || 8080 },
                      })
                    }
                  />
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1 block text-sm text-muted-foreground">用户名</label>
                  <Input
                    value={form.config.user}
                    onChange={(e) =>
                      setForm({ ...form, config: { ...form.config, user: e.target.value } })
                    }
                    placeholder="admin"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm text-muted-foreground">密码</label>
                  <Input
                    type="password"
                    value={form.config.password}
                    onChange={(e) =>
                      setForm({ ...form, config: { ...form.config, password: e.target.value } })
                    }
                    placeholder={editingId ? "留空表示不更新" : "******"}
                  />
                </div>
              </div>
            </div>
          </div>

          <DialogFooter className="mt-2 flex items-center justify-between sm:justify-between">
            <Button variant="outline" onClick={handleTestInDialog} disabled={testing || !form.config.host}>
              {testing ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Zap className="mr-2 size-4" />}
              测试连接
            </Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                取消
              </Button>
              <Button onClick={handleSave} disabled={saving || !form.name || !form.config.host}>
                {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
                {editingId ? "保存" : "创建"}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmActionDialog
        open={!!deleteTarget}
        title="删除服务"
        description={`确认删除服务「${deleteTarget?.name}」？此操作不可恢复。`}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        onConfirm={handleDelete}
        confirming={deleting}
      />
    </>
  )
}
