import { useCallback, useEffect, useMemo, useState } from "react"
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
import { useShellI18n } from "@/i18n/shellI18n"
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
  { value: "http_api", labelKey: "service.provider.http" as const, healthPath: "/health" },
  { value: "prometheus", label: "Prometheus", healthPath: "/-/ready" },
  { value: "alertmanager", label: "Alertmanager", healthPath: "/-/ready" },
  { value: "grafana", label: "Grafana", healthPath: "/api/health" },
] as const

const RESOURCE_TYPES = [
  { value: "cluster", labelKey: "service.resource.cluster" as const },
  { value: "datasource", labelKey: "service.resource.datasource" as const },
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

function formatCopy(copy: string, key: string, value: string | number): string {
  return copy.replace(`{${key}}`, String(value))
}

function parseHeaderMap(
  text: string,
  label: string,
  messages: { invalidJson: string; invalidObject: string; invalidValue: string },
): Record<string, string> {
  let value: unknown
  try {
    value = JSON.parse(text || "{}")
  } catch {
    throw new Error(formatCopy(messages.invalidJson, "label", label))
  }
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error(formatCopy(messages.invalidObject, "label", label))
  }
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== "string") {
      throw new Error(formatCopy(messages.invalidValue, "label", label))
    }
    result[key] = item
  }
  return result
}

function resolveResourceLabel(
  ref: string | null | undefined,
  datasourceById: Map<string, DataSource>,
  clusterKeySet: Set<string>,
  noneLabel: string,
  staleLabel: string,
): string {
  const { type, value } = parseResourceRef(ref)
  if (!value) return noneLabel
  if (type === "datasource") return datasourceById.get(value)?.name || staleLabel
  return clusterKeySet.has(value) ? value : staleLabel
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
  const { t } = useShellI18n()
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
      setError(getErrorMessage(cause, t("service.loadFailed")))
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
      const resource = resolveResourceLabel(
        service.resource_ref,
        datasourceById,
        clusterKeySet,
        t("service.resource.none"),
        t("service.resource.stale"),
      )
      return [service.name, service.service_type, service.config?.base_url || "", resource]
        .some((value) => value.toLowerCase().includes(query))
    })
  }, [clusterKeySet, datasourceById, providerFilter, searchQuery, services, t])

  const paged = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filtered.slice(start, start + PAGE_SIZE)
  }, [filtered, page])

  useEffect(() => setPage(1), [providerFilter, searchQuery])

  const revealAdvancedContent = useCallback((node: HTMLDivElement | null) => {
    node?.scrollIntoView({ block: "nearest" })
  }, [])

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
    const headerErrors = {
      invalidJson: t("service.error.invalidJson"),
      invalidObject: t("service.error.invalidObject"),
      invalidValue: t("service.error.invalidValue"),
    }
    const defaultHeaders = parseHeaderMap(
      form.defaultHeadersText,
      t("service.defaultHeaders"),
      headerErrors,
    )
    const secretHeaders = parseHeaderMap(
      form.secretHeadersText,
      t("service.secretHeaders"),
      headerErrors,
    )
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
        toast.success(t("service.updated"))
      } else {
        await servicesApi.create(payload)
        toast.success(t("service.created"))
      }
      setDialogOpen(false)
      await fetchData()
    } catch (cause) {
      toast.error(getErrorMessage(cause, t("service.saveFailed")))
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
        toast.success(`${t("service.connectSuccess")}${result.http_status ? ` · HTTP ${result.http_status}` : ""}`)
      } else {
        toast.error(result.message || t("service.connectFailed"))
      }
    } catch (cause) {
      toast.error(getErrorMessage(cause, t("service.testFailed")))
    } finally {
      setTesting(false)
    }
  }

  async function handleTestConnection(service: Service) {
    setRowTestingId(service.id)
    try {
      const result = await servicesApi.test(service.id)
      if (result.success) {
        toast.success(formatCopy(t("service.rowHealthy"), "name", service.name))
      } else {
        toast.error(result.message || t("service.connectFailed"))
      }
    } catch (cause) {
      toast.error(getErrorMessage(cause, t("service.testFailed")))
    } finally {
      setRowTestingId(null)
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await servicesApi.delete(deleteTarget.id)
      toast.success(t("service.deleted"))
      setDeleteTarget(null)
      await fetchData()
    } catch (cause) {
      toast.error(getErrorMessage(cause, t("service.deleteFailed")))
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
            <SelectItem value="all">{t("service.filterAll")}</SelectItem>
            {providerOptions.map((value) => (
              <SelectItem key={value} value={value}>
                {(() => {
                  const provider = PROVIDERS.find((item) => item.value === value)
                  return provider && "labelKey" in provider ? t(provider.labelKey) : provider?.label || value
                })()}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
          <Input
            aria-label={t("service.searchAria")}
            placeholder={t("service.searchPlaceholder")}
            className="w-72 rounded-lg bg-card pl-9 text-sm"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
          />
        </div>
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <Button variant="outline" size="sm" onClick={() => void fetchData()} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {t("service.refresh")}
        </Button>
        <Button size="sm" onClick={openCreateDialog}><Plus className="size-4" />{t("service.create")}</Button>
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
            <Button variant="outline" size="sm" onClick={() => void fetchData()}>{t("service.retry")}</Button>
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
              <p className="text-sm text-foreground">{searchQuery || providerFilter !== "all" ? t("service.emptyNoMatch") : t("service.emptyNone")}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t("service.emptyDesc")}</p>
            </div>
            {!searchQuery && providerFilter === "all" && <Button size="sm" onClick={openCreateDialog}>{t("service.emptyAdd")}</Button>}
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
                {service.status === "active" ? t("service.status.active") : t("service.status.inactive")}
                {service.has_credentials && <KeyRound className="ml-1 size-3" />}
              </div>
            </div>
          </div>
        </TableCell>
        <TableCell><Badge variant="outline">{(() => {
          const provider = PROVIDERS.find((item) => item.value === service.service_type)
          return provider && "labelKey" in provider ? t(provider.labelKey) : provider?.label || service.service_type
        })()}</Badge></TableCell>
        <TableCell className="max-w-64 truncate font-mono text-xs text-muted-foreground">{service.config?.base_url || "—"}</TableCell>
        <TableCell className="text-sm text-muted-foreground">{resolveResourceLabel(service.resource_ref, datasourceById, clusterKeySet, t("service.resource.none"), t("service.resource.stale"))}</TableCell>
        <TableCell>
          <div className="flex items-center gap-1.5 text-sm text-muted-foreground"><BookOpen className="size-3.5" />{service.knowledge_base_ids?.length || 0}</div>
        </TableCell>
        <TableCell className="text-right" onClick={(event) => event.stopPropagation()}>
          <div className="flex items-center justify-end gap-1">
            <ActionButton label={t("service.action.test")} variant="ghost" size="icon-xs" disabled={rowTestingId === service.id} onClick={() => void handleTestConnection(service)}>
              {rowTestingId === service.id ? <Loader2 className="size-3.5 animate-spin" /> : <Zap className="size-3.5" />}
            </ActionButton>
            <ActionButton label={t("service.action.edit")} variant="ghost" size="icon-xs" onClick={() => openEditDialog(service)}><Pencil className="size-3.5" /></ActionButton>
            <ActionButton label={t("service.action.delete")} variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive" onClick={() => setDeleteTarget(service)}><Trash2 className="size-3.5" /></ActionButton>
          </div>
        </TableCell>
      </TableRow>
    ))
  }

  const primary = (
    <div className="rounded-xl bg-card shadow-sm">
      <div className="flex items-center gap-4 border-b border-border px-5 py-3">
        <Tabs value="all" onValueChange={() => {}}><TabsList><TabsTrigger value="all">{t("service.tabAll")}</TabsTrigger></TabsList></Tabs>
        <span className="text-xs tabular-nums text-muted-foreground">{filtered.length} {t("service.resultCount")}</span>
      </div>
      <ListTable className="overflow-hidden rounded-none border-0">
        <Table>
          <TableHeader><TableRow className="hover:bg-transparent">
            <TableHead>{t("service.col.name")}</TableHead><TableHead>{t("service.col.type")}</TableHead><TableHead>{t("service.col.address")}</TableHead>
            <TableHead>{t("service.col.resource")}</TableHead><TableHead>{t("service.col.documents")}</TableHead><TableHead className="text-right">{t("service.col.actions")}</TableHead>
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
            <DialogTitle>{editingService ? t("service.dialogEdit") : t("service.dialogCreate")}</DialogTitle>
            <DialogDescription>{t("service.dialogDesc")}</DialogDescription>
          </DialogHeader>

          <ScrollArea className="h-0 min-h-0 flex-1 [&>[data-slot=scroll-area-viewport]]:absolute [&>[data-slot=scroll-area-viewport]]:inset-0">
            <div className="space-y-5 pr-4">
            <section className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-sm font-medium">{t("service.label.name")}</label>
                <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder={t("service.namePlaceholder")} />
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium">{t("service.label.type")}</label>
                <Select value={form.service_type} onValueChange={(value) => {
                  const preset = PROVIDERS.find((item) => item.value === value)
                  setForm((current) => ({ ...current, service_type: value, config: { ...current.config, health_check_path: preset?.healthPath || current.config.health_check_path } }))
                }}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PROVIDERS.map((item) => <SelectItem key={item.value} value={item.value}>{"labelKey" in item ? t(item.labelKey) : item.label}</SelectItem>)}
                    {editingService && !PROVIDERS.some((item) => item.value === editingService.service_type) && <SelectItem value={editingService.service_type}>{editingService.service_type}</SelectItem>}
                  </SelectContent>
                </Select>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-muted/10 p-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_160px]">
                <div>
                  <label className="mb-1.5 block text-sm font-medium">{t("service.label.baseUrl")}</label>
                  <Input value={form.config.base_url} onChange={(event) => updateConfig("base_url", event.target.value)} placeholder={t("service.example.baseUrl")} />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium">{t("service.label.response")}</label>
                  <Select value={form.config.response_format} onValueChange={(value) => updateConfig("response_format", value as ServiceHTTPConfig["response_format"])}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="auto">{t("service.response.auto")}</SelectItem><SelectItem value="json">{t("service.response.json")}</SelectItem><SelectItem value="text">{t("service.response.text")}</SelectItem></SelectContent>
                  </Select>
                </div>
              </div>
              <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_160px]">
                <div>
                  <label className="mb-1.5 block text-sm font-medium">{t("service.label.healthPath")}</label>
                  <Input value={form.config.health_check_path} onChange={(event) => updateConfig("health_check_path", event.target.value)} placeholder={t("service.example.healthPath")} />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium">{t("service.label.timeout")}</label>
                  <Input type="number" min={1} max={120} value={form.config.timeout_seconds} onChange={(event) => updateConfig("timeout_seconds", Number(event.target.value) || 30)} />
                </div>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-muted/10 p-4">
              <div className="mb-4 flex items-center gap-2"><KeyRound className="size-4 text-muted-foreground" /><h3 className="text-sm font-medium">{t("service.auth.title")}</h3>{editingService?.has_credentials && <Badge variant="secondary">{t("service.auth.saved")}</Badge>}</div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-sm font-medium">{t("service.auth.type")}</label>
                  <Select value={authType} onValueChange={(value) => updateAuthType(value as AuthType)}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="none">{t("service.auth.none")}</SelectItem><SelectItem value="basic">{t("service.auth.basic")}</SelectItem><SelectItem value="bearer">{t("service.auth.bearer")}</SelectItem><SelectItem value="api_key">{t("service.auth.apiKey")}</SelectItem></SelectContent>
                  </Select>
                </div>
                {authType === "basic" && <>
                  <div><label className="mb-1.5 block text-sm font-medium">{t("service.auth.username")}</label><Input autoComplete="off" value={form.secrets.username || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, username: event.target.value } })} /></div>
                  <div><label className="mb-1.5 block text-sm font-medium">{t("service.auth.password")}</label><Input type="password" autoComplete="new-password" value={form.secrets.password || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, password: event.target.value } })} placeholder={editingService?.has_credentials ? t("service.auth.keepPassword") : t("service.auth.enterPassword")} /></div>
                </>}
                {authType === "bearer" && <div><label className="mb-1.5 block text-sm font-medium">{t("service.auth.bearer")}</label><Input type="password" autoComplete="new-password" value={form.secrets.bearer_token || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, bearer_token: event.target.value } })} placeholder={editingService?.has_credentials ? t("service.auth.keepToken") : t("service.auth.enterToken")} /></div>}
                {authType === "api_key" && <>
                  <div><label className="mb-1.5 block text-sm font-medium">{t("service.auth.headerName")}</label><Input value={form.config.api_key_header} onChange={(event) => updateConfig("api_key_header", event.target.value)} /></div>
                  <div><label className="mb-1.5 block text-sm font-medium">{t("service.auth.apiKey")}</label><Input type="password" autoComplete="new-password" value={form.secrets.api_key || ""} onChange={(event) => setForm({ ...form, secrets: { ...form.secrets, api_key: event.target.value } })} placeholder={editingService?.has_credentials ? t("service.auth.keepKey") : t("service.auth.enterKey")} /></div>
                </>}
              </div>
            </section>

            <section className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-sm font-medium">{t("service.resource.label")}</label>
                <div className="grid grid-cols-[120px_1fr] gap-2">
                  <Select value={form.resourceType} onValueChange={(value) => setForm({ ...form, resourceType: value as ResourceRefType, resourceValue: "" })}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>{RESOURCE_TYPES.map((item) => <SelectItem key={item.value} value={item.value}>{t(item.labelKey)}</SelectItem>)}</SelectContent>
                  </Select>
                  <Select value={form.resourceValue || "__none__"} onValueChange={(value) => setForm({ ...form, resourceValue: value === "__none__" ? "" : value })}>
                    <SelectTrigger className="w-full"><SelectValue placeholder={t("service.resource.select")} /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">{t("service.resource.doNotLink")}</SelectItem>
                      {form.resourceType === "cluster"
                        ? clusterKeys.map((key) => <SelectItem key={key} value={key}>{key}</SelectItem>)
                        : datasources.map((item) => <SelectItem key={item.id} value={String(item.id)}>{item.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="flex items-end pb-2">
                <div className="flex w-full items-center justify-between rounded-lg border border-border px-3 py-2">
                  <div className="flex items-center gap-2"><ShieldCheck className="size-4 text-muted-foreground" /><div><p className="text-sm font-medium">{t("service.tls.title")}</p><p className="text-xs text-muted-foreground">{t("service.tls.hint")}</p></div></div>
                  <Switch checked={form.config.verify_tls} onCheckedChange={(checked) => updateConfig("verify_tls", checked)} />
                </div>
              </div>
            </section>

            <section>
              <div className="mb-2 flex items-center gap-2"><BookOpen className="size-4 text-muted-foreground" /><h3 className="text-sm font-medium">{t("service.docs.title")}</h3></div>
              {knowledgeBases.length ? <ScrollArea className="h-40 rounded-lg border border-border"><div className="grid gap-2 p-3 sm:grid-cols-2">
                {knowledgeBases.map((kb) => <label key={kb.id} className="flex cursor-pointer items-start gap-2 rounded-lg p-2 transition-colors hover:bg-muted/50">
                  <Checkbox checked={form.knowledge_base_ids.includes(kb.id)} onCheckedChange={(checked) => toggleKnowledgeBase(kb.id, checked === true)} />
                  <span className="min-w-0"><span className="block truncate text-sm font-medium">{kb.name}</span><span className="text-xs text-muted-foreground">{formatCopy(t("service.docs.count"), "count", kb.document_count)}</span></span>
                </label>)}
              </div></ScrollArea> : <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">{t("service.docs.empty")}</div>}
            </section>

            <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
              <CollapsibleTrigger asChild><Button variant="ghost" className="w-full justify-between">{t("service.advanced")}<ChevronDown className={`size-4 transition-transform ${advancedOpen ? "rotate-180" : ""}`} /></Button></CollapsibleTrigger>
              <CollapsibleContent className="pt-3">
                <div ref={revealAdvancedContent} className="grid gap-4 sm:grid-cols-2">
                  <div><label htmlFor="service-default-headers" className="mb-1.5 block text-sm font-medium">{t("service.defaultHeadersJson")}</label><Textarea id="service-default-headers" className="min-h-28 font-mono text-xs" value={form.defaultHeadersText} onChange={(event) => setForm({ ...form, defaultHeadersText: event.target.value })} /></div>
                  <div><label htmlFor="service-secret-headers" className="mb-1.5 block text-sm font-medium">{t("service.secretHeadersJson")}</label><Textarea id="service-secret-headers" className="min-h-28 font-mono text-xs" value={form.secretHeadersText} onChange={(event) => setForm({ ...form, secretHeadersText: event.target.value })} placeholder={'{"X-Custom-Token":"..."}'} /></div>
                  <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2 sm:col-span-2">
                    <div><p className="text-sm font-medium">{t("service.proxy.title")}</p><p className="text-xs text-muted-foreground">{t("service.proxy.hint")}</p></div>
                    <Switch checked={form.config.use_environment_proxy} onCheckedChange={(checked) => updateConfig("use_environment_proxy", checked)} />
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
            </div>
          </ScrollArea>

          <DialogFooter className="mt-2 flex items-center justify-between sm:justify-between">
            <Button variant="outline" onClick={() => void handleTestInDialog()} disabled={testing || !form.config.base_url}>
              {testing ? <Loader2 className="size-4 animate-spin" /> : <Zap className="size-4" />}{t("service.action.test")}
            </Button>
            <div className="flex gap-2"><Button variant="outline" onClick={() => setDialogOpen(false)}>{t("service.cancel")}</Button><Button onClick={() => void handleSave()} disabled={saving || !form.name.trim() || !form.config.base_url.trim()}>{saving && <Loader2 className="size-4 animate-spin" />}{editingService ? t("service.save") : t("service.submitCreate")}</Button></div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmActionDialog open={!!deleteTarget} title={t("service.deleteTitle")} description={formatCopy(t("service.deleteDesc"), "name", deleteTarget?.name || "")} onOpenChange={(open) => !open && setDeleteTarget(null)} onConfirm={handleDelete} confirming={deleting} />
    </TooltipProvider>
  )
}
