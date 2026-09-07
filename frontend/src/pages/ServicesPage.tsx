import { useEffect, useMemo, useState } from "react"
import {
  BookOpen,
  ChevronDown,
  KeyRound,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Zap,
} from "lucide-react"
import { toast } from "sonner"

import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
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
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { datasourcesApi, knowledgeApi, servicesApi } from "@/lib/api"
import type {
  DataSource,
  KnowledgeBase,
  Service,
  ServiceHTTPConfig,
  ServiceInput,
  ServiceSecretConfig,
} from "@/lib/api"

const PAGE_SIZE = 10

const PROVIDERS = [
  { value: "http_api", label: "通用 HTTP API", healthPath: "/health" },
  { value: "prometheus", label: "Prometheus", healthPath: "/-/ready" },
  { value: "alertmanager", label: "Alertmanager", healthPath: "/-/ready" },
  { value: "grafana", label: "Grafana", healthPath: "/api/health" },
] as const

const RESOURCE_TYPES = [
  { value: "cluster", label: "集群" },
  { value: "datasource", label: "数据源" },
] as const

type ResourceRefType = (typeof RESOURCE_TYPES)[number]["value"]
type AuthType = ServiceHTTPConfig["auth_type"]

type ServiceFormState = {
  name: string
  service_type: string
  resourceType: ResourceRefType
  resourceValue: string
  config: ServiceHTTPConfig
  secrets: ServiceSecretConfig
  knowledge_base_ids: number[]
  defaultHeadersText: string
  secretHeadersText: string
}

const emptyConfig: ServiceHTTPConfig = {
  base_url: "",
  auth_type: "none",
  api_key_header: "X-API-Key",
  default_headers: {},
  health_check_path: "/health",
  health_check_method: "GET",
  response_format: "auto",
  timeout_seconds: 30,
  verify_tls: true,
  use_environment_proxy: false,
  max_response_bytes: 262144,
}

const emptySecrets: ServiceSecretConfig = {
  username: "",
  password: "",
  bearer_token: "",
  api_key: "",
  headers: {},
}

function emptyForm(): ServiceFormState {
  return {
    name: "",
    service_type: "http_api",
    resourceType: "cluster",
    resourceValue: "",
    config: { ...emptyConfig },
    secrets: { ...emptySecrets },
    knowledge_base_ids: [],
    defaultHeadersText: "{}",
    secretHeadersText: "{}",
  }
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message
  }
  return fallback
}

function parseResourceRef(ref: string | null | undefined): { type: ResourceRefType; value: string } {
  if (!ref) return { type: "cluster", value: "" }
  const [prefix, ...rest] = ref.split(":")
  const value = rest.join(":")
  if ((prefix === "cluster" || prefix === "datasource") && value) {
    return { type: prefix, value }
  }
  return { type: "cluster", value: ref }
}

function buildResourceRef(type: ResourceRefType, value: string): string | undefined {
  const normalized = value.trim()
  return normalized ? `${type}:${normalized}` : undefined
}

function parseHeaderMap(text: string, label: string): Record<string, string> {
  let value: unknown
  try {
    value = JSON.parse(text || "{}")
  } catch {
    throw new Error(`${label}必须是有效 JSON`)
  }
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error(`${label}必须是键值对象`)
  }
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== "string") throw new Error(`${label}中的值必须是字符串`)
    result[key] = item
  }
  return result
}

function resolveResourceLabel(
  ref: string | null | undefined,
  datasourceById: Map<string, DataSource>,
  clusterKeySet: Set<string>,
): string {
  const { type, value } = parseResourceRef(ref)
  if (!value) return "未关联"
  if (type === "datasource") return datasourceById.get(value)?.name || "引用已失效"
  return clusterKeySet.has(value) ? value : "引用已失效"
}

function ActionButton({
  label,
  children,
  ...props
}: React.ComponentProps<typeof Button> & { label: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button aria-label={label} {...props}>{children}</Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

export function ServicesPage() {
  const [services, setServices] = useState<Service[]>([])
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [providerFilter, setProviderFilter] = useState("all")
  const [page, setPage] = useState(1)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingService, setEditingService] = useState<Service | null>(null)
  const [form, setForm] = useState<ServiceFormState>(emptyForm)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [rowTestingId, setRowTestingId] = useState<number | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Service | null>(null)
  const [deleting, setDeleting] = useState(false)

  const clusterKeys = useMemo(
    () => Array.from(new Set(datasources.map((item) => item.cluster_key).filter(Boolean))).sort(),
    [datasources],
  )
  const clusterKeySet = useMemo(() => new Set(clusterKeys), [clusterKeys])
  const datasourceById = useMemo(
    () => new Map(datasources.map((item) => [String(item.id), item])),
    [datasources],
  )
  async function fetchData() {
    setLoading(true)
    setError(null)
    try {
      const [serviceList, datasourceList, kbList] = await Promise.all([
        servicesApi.list(),
        datasourcesApi.list(),
        knowledgeApi.list(),
      ])
      setServices(Array.isArray(serviceList) ? serviceList : [])
      setDatasources(Array.isArray(datasourceList) ? datasourceList : [])
      setKnowledgeBases(Array.isArray(kbList) ? kbList : [])
    } catch (cause) {
      setError(getErrorMessage(cause, "服务列表加载失败"))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void fetchData() }, [])

  const filtered = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    return services.filter((service) => {
      if (providerFilter !== "all" && service.service_type !== providerFilter) return false
      if (!query) return true
      const resource = resolveResourceLabel(service.resource_ref, datasourceById, clusterKeySet)
      return [service.name, service.service_type, service.config?.base_url || "", resource]
        .some((value) => value.toLowerCase().includes(query))
    })
  }, [clusterKeySet, datasourceById, providerFilter, searchQuery, services])

  const paged = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filtered.slice(start, start + PAGE_SIZE)
  }, [filtered, page])

  useEffect(() => setPage(1), [providerFilter, searchQuery])

  function openCreateDialog() {
    setEditingService(null)
    setForm(emptyForm())
    setAdvancedOpen(false)
    setDialogOpen(true)
  }

  function openEditDialog(service: Service) {
    const resource = parseResourceRef(service.resource_ref)
    setEditingService(service)
    setForm({
      name: service.name,
      service_type: service.service_type,
      resourceType: resource.type,
      resourceValue: resource.value,
      config: { ...emptyConfig, ...(service.config || {}) },
      secrets: { ...emptySecrets },
      knowledge_base_ids: service.knowledge_base_ids || [],
      defaultHeadersText: JSON.stringify(service.config?.default_headers || {}, null, 2),
      secretHeadersText: "{}",
    })
    setAdvancedOpen(false)
    setDialogOpen(true)
  }

  function buildPayload(): ServiceInput {
    const defaultHeaders = parseHeaderMap(form.defaultHeadersText, "默认 Header")
    const secretHeaders = parseHeaderMap(form.secretHeadersText, "加密 Header")
    return {
      name: form.name.trim(),
      service_type: form.service_type,
      resource_ref: buildResourceRef(form.resourceType, form.resourceValue),
      knowledge_base_ids: form.knowledge_base_ids,
      config: { ...form.config, base_url: form.config.base_url.trim(), default_headers: defaultHeaders },
      secrets: { ...form.secrets, headers: secretHeaders },
    }
  }

  async function handleSave() {
    setSaving(true)
    try {
      const payload = buildPayload()
      if (editingService) {
        await servicesApi.update(editingService.id, payload)
        toast.success("服务配置已更新")
      } else {
        await servicesApi.create(payload)
        toast.success("外部服务已创建")
      }
      setDialogOpen(false)
      await fetchData()
    } catch (cause) {
      toast.error(getErrorMessage(cause, "保存失败"))
    } finally {
      setSaving(false)
    }
  }

  async function handleTestInDialog() {
    setTesting(true)
    try {
      const result = editingService
        ? await servicesApi.testUpdateConfig(editingService.id, buildPayload())
        : await servicesApi.testConfig(buildPayload())
      if (result.success) {
        toast.success(`连接成功${result.http_status ? ` · HTTP ${result.http_status}` : ""}`)
      } else {
        toast.error(result.message || "连接失败")
      }
    } catch (cause) {
      toast.error(getErrorMessage(cause, "测试连接失败"))
    } finally {
      setTesting(false)
    }
  }

  async function handleTestConnection(service: Service) {
    setRowTestingId(service.id)
    try {
      const result = await servicesApi.test(service.id)
      if (result.success) {
        toast.success(`${service.name} 连接正常`)
      } else {
        toast.error(result.message || "连接失败")
      }
    } catch (cause) {
      toast.error(getErrorMessage(cause, "测试连接失败"))
    } finally {
      setRowTestingId(null)
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await servicesApi.delete(deleteTarget.id)
      toast.success("服务已删除")
      setDeleteTarget(null)
      await fetchData()
    } catch (cause) {
      toast.error(getErrorMessage(cause, "删除失败"))
    } finally {
      setDeleting(false)
    }
  }

  function updateConfig<K extends keyof ServiceHTTPConfig>(key: K, value: ServiceHTTPConfig[K]) {
    setForm((current) => ({ ...current, config: { ...current.config, [key]: value } }))
  }

  function updateAuthType(value: AuthType) {
    updateConfig("auth_type", value)
  }

  function toggleKnowledgeBase(id: number, checked: boolean) {
    setForm((current) => ({
      ...current,
      knowledge_base_ids: checked
        ? Array.from(new Set([...current.knowledge_base_ids, id]))
        : current.knowledge_base_ids.filter((item) => item !== id),
    }))
  }

  const providerOptions = useMemo(() => {
    const values = new Set(PROVIDERS.map((item) => item.value as string))
    for (const service of services) values.add(service.service_type)
    return Array.from(values)
  }, [services])

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <Select value={providerFilter} onValueChange={setProviderFilter}>
          <SelectTrigger className="w-44 bg-card"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            {providerOptions.map((value) => (
              <SelectItem key={value} value={value}>
                {PROVIDERS.find((item) => item.value === value)?.label || value}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            aria-label="搜索服务"
            placeholder="搜索名称、地址或关联资源"
            className="w-72 rounded-lg bg-card pl-9 text-sm"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
          />
        </div>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button variant="outline" size="sm" onClick={() => void fetchData()} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
        <Button size="sm" onClick={openCreateDialog}><Plus className="size-4" />新增服务</Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  const columnCount = 6
  function renderTableBody() {
    if (loading) return <ListTableLoadingRows rowCount={6} columnCount={columnCount} />
    if (error) {
      return (
        <TableRow><TableCell colSpan={columnCount} className="h-36 text-center">
          <div className="flex flex-col items-center gap-3">
            <Plug className="size-8 text-negative/60" />
            <p className="text-sm text-negative">{error}</p>
            <Button variant="outline" size="sm" onClick={() => void fetchData()}>重试</Button>
          </div>
        </TableCell></TableRow>
      )
    }
    if (!filtered.length) {
      return (
        <TableRow><TableCell colSpan={columnCount} className="h-40 text-center">
          <div className="flex flex-col items-center gap-3">
            <Plug className="size-8 text-muted-foreground/40" />
            <div>
              <p className="text-sm text-foreground">{searchQuery || providerFilter !== "all" ? "没有匹配的服务" : "还没有外部服务"}</p>
              <p className="mt-1 text-xs text-muted-foreground">关联监控或客户自建 API，让 Chat 获得数据库之外的证据。</p>
            </div>
            {!searchQuery && providerFilter === "all" && <Button size="sm" onClick={openCreateDialog}>添加第一个服务</Button>}
          </div>
        </TableCell></TableRow>
      )
    }
    return paged.map((service) => (
      <TableRow key={service.id} className="cursor-pointer transition-colors duration-150 hover:bg-muted/40" onClick={() => openEditDialog(service)}>
        <TableCell>
          <div className="flex items-center gap-3">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10"><Plug className="size-4 text-primary" /></div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{service.name}</p>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className={`size-1.5 rounded-full ${service.status === "active" ? "bg-positive" : "bg-muted-foreground"}`} />
                {service.status === "active" ? "可用" : "已停用"}
                {service.has_credentials && <KeyRound className="ml-1 size-3" />}
              </div>
            </div>
          </div>
        </TableCell>
        <TableCell><Badge variant="outline">{PROVIDERS.find((item) => item.value === service.service_type)?.label || service.service_type}</Badge></TableCell>
        <TableCell className="max-w-64 truncate font-mono text-xs text-muted-foreground">{service.config?.base_url || "—"}</TableCell>
        <TableCell className="text-sm text-muted-foreground">{resolveResourceLabel(service.resource_ref, datasourceById, clusterKeySet)}</TableCell>
        <TableCell>
          <div className="flex items-center gap-1.5 text-sm text-muted-foreground"><BookOpen className="size-3.5" />{service.knowledge_base_ids?.length || 0}</div>
        </TableCell>
        <TableCell className="text-right" onClick={(event) => event.stopPropagation()}>
          <div className="flex items-center justify-end gap-1">
            <ActionButton label="测试连接" variant="ghost" size="icon-xs" disabled={rowTestingId === service.id} onClick={() => void handleTestConnection(service)}>
              {rowTestingId === service.id ? <Loader2 className="size-3.5 animate-spin" /> : <Zap className="size-3.5" />}
            </ActionButton>
            <ActionButton label="编辑服务" variant="ghost" size="icon-xs" onClick={() => openEditDialog(service)}><Pencil className="size-3.5" /></ActionButton>
            <ActionButton label="删除服务" variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive" onClick={() => setDeleteTarget(service)}><Trash2 className="size-3.5" /></ActionButton>
          </div>
        </TableCell>
      </TableRow>
    ))
  }

  const primary = (
    <div className="rounded-xl bg-card shadow-sm">
      <div className="flex items-center gap-4 border-b border-border px-5 py-3">
        <Tabs value="all" onValueChange={() => {}}><TabsList><TabsTrigger value="all">外部服务</TabsTrigger></TabsList></Tabs>
        <span className="text-xs tabular-nums text-muted-foreground">{filtered.length} 项结果</span>
      </div>
      <ListTable className="overflow-hidden rounded-none border-0">
        <Table>
          <TableHeader><TableRow className="hover:bg-transparent">
            <TableHead>名称</TableHead><TableHead>类型</TableHead><TableHead>API 地址</TableHead>
            <TableHead>关联资源</TableHead><TableHead>文档</TableHead><TableHead className="text-right">操作</TableHead>
          </TableRow></TableHeader>
          <TableBody>{renderTableBody()}</TableBody>
        </Table>
        <PaginationFooter page={page} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
      </ListTable>
    </div>
  )

  const authType = form.config.auth_type
  return (
    <TooltipProvider>
      <WorkbenchPage toolbar={toolbar} primary={primary} />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="flex h-[90vh] max-h-192 flex-col overflow-hidden sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingService ? "编辑外部服务" : "连接外部服务"}</DialogTitle>
            <DialogDescription>配置通用 HTTP API，并把服务关联到数据源及其文档。</DialogDescription>
          </DialogHeader>

          <ScrollArea className="h-0 min-h-0 flex-1 [&>[data-slot=scroll-area-viewport]]:absolute [&>[data-slot=scroll-area-viewport]]:inset-0">
            <div className="space-y-5 pr-4">
            <section className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-sm font-medium">名称</label>
                <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="生产 Prometheus" />
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium">服务类型</label>
                <Select value={form.service_type} onValueChange={(value) => {
                  const preset = PROVIDERS.find((item) => item.value === value)
                  setForm((current) => ({ ...current, service_type: value, config: { ...current.config, health_check_path: preset?.healthPath || current.config.health_check_path } }))
                }}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PROVIDERS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
                    {editingService && !PROVIDERS.some((item) => item.value === editingService.service_type) && <SelectItem value={editingService.service_type}>{editingService.service_type}</SelectItem>}
                  </SelectContent>
                </Select>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-muted/10 p-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_160px]">
                <div>
                  <label className="mb-1.5 block text-sm font-medium">Base URL</label>
                  <Input value={form.config.base_url} onChange={(event) => updateConfig("base_url", event.target.value)} placeholder="http://prometheus:9090" />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium">响应格式</label>
                  <Select value={form.config.response_format} onValueChange={(value) => updateConfig("response_format", value as ServiceHTTPConfig["response_format"])}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="auto">自动识别</SelectItem><SelectItem value="json">JSON</SelectItem><SelectItem value="text">文本</SelectItem></SelectContent>
                  </Select>
                </div>
              </div>
              <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_160px]">
                <div>
                  <label className="mb-1.5 block text-sm font-medium">健康检查路径</label>
                  <Input value={form.config.health_check_path} onChange={(event) => updateConfig("health_check_path", event.target.value)} placeholder="/-/ready" />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium">超时（秒）</label>
                  <Input type="number" min={1} max={120} value={form.config.timeout_seconds} onChange={(event) => updateConfig("timeout_seconds", Number(event.target.value) || 30)} />
                </div>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-muted/10 p-4">
              <div className="mb-4 flex items-center gap-2"><KeyRound className="size-4 text-muted-foreground" /><h3 className="text-sm font-medium">认证</h3>{editingService?.has_credentials && <Badge variant="secondary">已保存凭据</Badge>}</div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-sm font-medium">认证方式</label>
                  <Select value={authType} onValueChange={(value) => updateAuthType(value as AuthType)}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="none">无需认证</SelectItem><SelectItem value="basic">Basic Auth</SelectItem><SelectItem value="bearer">Bearer Token</SelectItem><SelectItem value="api_key">API Key</SelectItem></SelectContent>
                  </Select>
                </div>
                {authType === "basic" && <>
                  <div><label className="mb-1.5 block text-sm font-medium">用户名</label><Input autoComplete="off" value={form.secrets.username || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, username: event.target.value } })} /></div>
                  <div><label className="mb-1.5 block text-sm font-medium">密码</label><Input type="password" autoComplete="new-password" value={form.secrets.password || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, password: event.target.value } })} placeholder={editingService?.has_credentials ? "留空保留原密码" : "输入密码"} /></div>
                </>}
                {authType === "bearer" && <div><label className="mb-1.5 block text-sm font-medium">Bearer Token</label><Input type="password" autoComplete="new-password" value={form.secrets.bearer_token || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, bearer_token: event.target.value } })} placeholder={editingService?.has_credentials ? "留空保留原 Token" : "输入 Token"} /></div>}
                {authType === "api_key" && <>
                  <div><label className="mb-1.5 block text-sm font-medium">Header 名称</label><Input value={form.config.api_key_header} onChange={(event) => updateConfig("api_key_header", event.target.value)} /></div>
                  <div><label className="mb-1.5 block text-sm font-medium">API Key</label><Input type="password" autoComplete="new-password" value={form.secrets.api_key || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, api_key: event.target.value } })} placeholder={editingService?.has_credentials ? "留空保留原 Key" : "输入 API Key"} /></div>
                </>}
              </div>
            </section>

            <section className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-sm font-medium">关联资源</label>
                <div className="grid grid-cols-[120px_1fr] gap-2">
                  <Select value={form.resourceType} onValueChange={(value) => setForm({ ...form, resourceType: value as ResourceRefType, resourceValue: "" })}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>{RESOURCE_TYPES.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
                  </Select>
                  <Select value={form.resourceValue || "__none__"} onValueChange={(value) => setForm({ ...form, resourceValue: value === "__none__" ? "" : value })}>
                    <SelectTrigger className="w-full"><SelectValue placeholder="选择关联资源" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">不关联</SelectItem>
                      {form.resourceType === "cluster"
                        ? clusterKeys.map((key) => <SelectItem key={key} value={key}>{key}</SelectItem>)
                        : datasources.map((item) => <SelectItem key={item.id} value={String(item.id)}>{item.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="flex items-end pb-2">
                <div className="flex w-full items-center justify-between rounded-lg border border-border px-3 py-2">
                  <div className="flex items-center gap-2"><ShieldCheck className="size-4 text-muted-foreground" /><div><p className="text-sm font-medium">校验 TLS 证书</p><p className="text-xs text-muted-foreground">生产环境建议保持开启</p></div></div>
                  <Switch checked={form.config.verify_tls} onCheckedChange={(checked) => updateConfig("verify_tls", checked)} />
                </div>
              </div>
            </section>

            <section>
              <div className="mb-2 flex items-center gap-2"><BookOpen className="size-4 text-muted-foreground" /><h3 className="text-sm font-medium">关联 API 文档</h3></div>
              {knowledgeBases.length ? <ScrollArea className="h-40 rounded-lg border border-border"><div className="grid gap-2 p-3 sm:grid-cols-2">
                {knowledgeBases.map((kb) => <label key={kb.id} className="flex cursor-pointer items-start gap-2 rounded-lg p-2 transition-colors hover:bg-muted/50">
                  <Checkbox checked={form.knowledge_base_ids.includes(kb.id)} onCheckedChange={(checked) => toggleKnowledgeBase(kb.id, checked === true)} />
                  <span className="min-w-0"><span className="block truncate text-sm font-medium">{kb.name}</span><span className="text-xs text-muted-foreground">{kb.document_count} 篇文档</span></span>
                </label>)}
              </div></ScrollArea> : <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">请先在知识库页面安装或上传对应 API 文档。</div>}
            </section>

            <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
              <CollapsibleTrigger asChild><Button variant="ghost" className="w-full justify-between">高级 Header 配置<ChevronDown className={`size-4 transition-transform ${advancedOpen ? "rotate-180" : ""}`} /></Button></CollapsibleTrigger>
              <CollapsibleContent className="grid gap-4 pt-3 sm:grid-cols-2">
                <div><label className="mb-1.5 block text-sm font-medium">默认 Header（JSON）</label><Textarea className="min-h-28 font-mono text-xs" value={form.defaultHeadersText} onChange={(event) => setForm({ ...form, defaultHeadersText: event.target.value })} /></div>
                <div><label className="mb-1.5 block text-sm font-medium">加密 Header（JSON）</label><Textarea className="min-h-28 font-mono text-xs" value={form.secretHeadersText} onChange={(event) => setForm({ ...form, secretHeadersText: event.target.value })} placeholder={'{"X-Custom-Token":"..."}'} /></div>
                <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2 sm:col-span-2">
                  <div><p className="text-sm font-medium">继承服务端环境代理</p><p className="text-xs text-muted-foreground">仅外部 API 需要 HTTP(S) 代理时开启；内网服务建议关闭。</p></div>
                  <Switch checked={form.config.use_environment_proxy} onCheckedChange={(checked) => updateConfig("use_environment_proxy", checked)} />
                </div>
              </CollapsibleContent>
            </Collapsible>
            </div>
          </ScrollArea>

          <DialogFooter className="mt-2 flex items-center justify-between sm:justify-between">
            <Button variant="outline" onClick={() => void handleTestInDialog()} disabled={testing || !form.config.base_url}>
              {testing ? <Loader2 className="size-4 animate-spin" /> : <Zap className="size-4" />}测试连接
            </Button>
            <div className="flex gap-2"><Button variant="outline" onClick={() => setDialogOpen(false)}>取消</Button><Button onClick={() => void handleSave()} disabled={saving || !form.name.trim() || !form.config.base_url.trim()}>{saving && <Loader2 className="size-4 animate-spin" />}{editingService ? "保存" : "创建"}</Button></div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmActionDialog open={!!deleteTarget} title="删除服务" description={`确认删除服务「${deleteTarget?.name}」？关联关系也会被移除。`} onOpenChange={(open) => !open && setDeleteTarget(null)} onConfirm={handleDelete} confirming={deleting} />
    </TooltipProvider>
  )
}
