import { useEffect, useMemo, useState } from "react"
import { Wrench, ChevronRight, Search, X } from "lucide-react"
import { useShellI18n } from "@/i18n/shellI18n"
import { capabilitiesApi, type CapabilitiesResponse, type ToolInfo } from "@/lib/api"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Drawer, DrawerContent, DrawerHeader, DrawerBody, DrawerTitle, DrawerDescription } from "@/components/ui/drawer"
import { Button } from "@/components/ui/button"

type SelectedItem =
  | { kind: "tool"; data: ToolInfo }

function ParamSchema({ properties, required }: { properties: Record<string, Record<string, unknown>>; required: string[] }) {
  const { t } = useShellI18n()
  const entries = Object.entries(properties)
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">{t("capabilities.drawer.noParams")}</p>
  }
  return (
    <div className="space-y-3">
      {entries.map(([name, schema]) => {
        const isRequired = required.includes(name)
        const typeStr = String(schema.type || "")
        const desc = String(schema.description || "")
        const enumValues = Array.isArray(schema.enum) ? schema.enum : null
        return (
          <div key={name} className="rounded-lg border border-border bg-muted/10 p-3">
            <div className="flex items-center gap-2 flex-wrap">
              <code className="text-sm font-medium text-foreground">{name}</code>
              {typeStr && (
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-normal">{typeStr}</Badge>
              )}
              {isRequired && (
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-normal text-negative border-negative/30 bg-negative/10">
                  {t("capabilities.drawer.required")}
                </Badge>
              )}
            </div>
            {desc && <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed">{desc}</p>}
            {enumValues && (
              <div className="mt-2 flex flex-wrap gap-1">
                {enumValues.map((v: string) => (
                  <code key={v} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{String(v)}</code>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function DetailDrawer({ selected, open, onOpenChange }: { selected: SelectedItem | null; open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useShellI18n()
  if (!selected) return null

  const title = selected.data.name
  const description = selected.data.description

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent showCloseButton={false} className="max-w-[560px]">
        <DrawerHeader className="shrink-0 border-b border-border px-5 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 min-w-0">
              <DrawerTitle className="truncate text-sm font-semibold">{title}</DrawerTitle>
              <Badge variant="outline" className="shrink-0 text-[10px] px-1.5 py-0 font-normal">
                Tool
              </Badge>
            </div>
            <Button variant="ghost" size="sm" className="shrink-0 size-7 p-0 text-muted-foreground hover:text-foreground" onClick={() => onOpenChange(false)}>
              <X className="size-4" />
              <span className="sr-only">Close</span>
            </Button>
          </div>
          <DrawerDescription className="sr-only">{description}</DrawerDescription>
        </DrawerHeader>
        <DrawerBody className="space-y-5 overflow-auto">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{t("capabilities.drawer.description")}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-foreground leading-relaxed">{description}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{t("capabilities.drawer.params")}</CardTitle>
            </CardHeader>
            <CardContent>
              <ParamSchema
                properties={(selected.data.parameters?.properties ?? {}) as Record<string, Record<string, unknown>>}
                required={Array.isArray(selected.data.parameters?.required) ? (selected.data.parameters.required as string[]) : []}
              />
            </CardContent>
          </Card>
        </DrawerBody>
      </DrawerContent>
    </Drawer>
  )
}

function ToolsSection({ tools, loading, onSelect }: { tools: ToolInfo[]; loading: boolean; onSelect: (item: SelectedItem) => void }) {
  const { t } = useShellI18n()
  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 duration-500 rounded-xl bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b border-border px-5 py-3">
        <Wrench className="size-4 text-muted-foreground" />
        <div>
          <div className="text-sm font-medium text-foreground">{t("capabilities.section.tools")}</div>
          <div className="text-xs text-muted-foreground">{t("capabilities.section.toolsDesc")}</div>
        </div>
      </div>
      <ListTable className="border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">{t("capabilities.col.name")}</TableHead>
              <TableHead>{t("capabilities.col.description")}</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <ListTableLoadingRows rowCount={4} columnCount={3} />
            ) : tools.length === 0 ? (
              <TableRow>
                <TableCell colSpan={3} className="h-32 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <Wrench className="size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">{t("capabilities.empty")}</p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              tools.map((tool) => (
                <TableRow
                  key={tool.name}
                  className="cursor-pointer transition-colors duration-150 hover:bg-muted/40"
                  onClick={() => onSelect({ kind: "tool", data: tool })}
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="flex size-7 items-center justify-center rounded-md bg-muted/50">
                        <Wrench className="size-3.5 text-muted-foreground" />
                      </div>
                      <span className="font-mono text-sm font-medium">{tool.name}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground leading-relaxed max-w-lg"><span className="line-clamp-2">{tool.description}</span></TableCell>
                  <TableCell>
                    <Button variant="ghost" size="sm" className="size-7 p-0">
                      <ChevronRight className="size-4 text-muted-foreground" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </ListTable>
    </div>
  )
}

export function CapabilitiesPage() {
  const { t } = useShellI18n()
  const [data, setData] = useState<CapabilitiesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<SelectedItem | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const pageSize = 20

  useEffect(() => {
    capabilitiesApi.list().then(setData).catch((err: Error) => setError(err.message))
  }, [])

  const loading = data === null && error === null

  const allItems = useMemo(() => {
    if (!data) return []
    return data.tools.map((d): SelectedItem => ({ kind: "tool" as const, data: d }))
  }, [data])

  const filtered = useMemo(() => {
    if (!search.trim()) return allItems
    const q = search.toLowerCase()
    return allItems.filter((item) => {
      return item.data.name.toLowerCase().includes(q) || item.data.description.toLowerCase().includes(q)
    })
  }, [allItems, search])

  const filteredTools = useMemo(() => filtered.map((i) => i.data), [filtered])
  const total = filtered.length

  function handleSelect(item: SelectedItem) {
    setSelected(item)
    setDrawerOpen(true)
  }

  if (error) {
    return (
      <WorkbenchPage
        className="mx-auto max-w-5xl"
        primary={
          <div className="rounded-xl bg-card shadow-sm p-8 text-center">
            <p className="text-sm text-destructive">{t("capabilities.error")}</p>
            <p className="mt-1 text-xs text-muted-foreground">{error}</p>
          </div>
        }
      />
    )
  }

  return (
    <WorkbenchPage
      className="mx-auto max-w-5xl"
      toolbar={
        <div className="rounded-xl bg-card p-4 shadow-sm">
          <FilterToolbar>
            <FilterToolbarGroup>
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
                <Input
                  className="w-72 rounded-lg bg-card pl-9 text-sm"
                  placeholder={t("capabilities.search")}
                  value={search}
                  onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                />
              </div>
            </FilterToolbarGroup>
            <FilterToolbarGroup>
              <span className="text-xs tabular-nums text-muted-foreground">{total} {t("capabilities.items")}</span>
            </FilterToolbarGroup>
          </FilterToolbar>
        </div>
      }
      primary={
        <div className="space-y-6">
          <ToolsSection tools={filteredTools} loading={loading} onSelect={handleSelect} />
          <PaginationFooter page={page} pageSize={pageSize} total={total} onPageChange={setPage} className="border-t border-border px-4 py-2" />
          <DetailDrawer selected={selected} open={drawerOpen} onOpenChange={setDrawerOpen} />
        </div>
      }
    />
  )
}
