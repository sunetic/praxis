import { useEffect, useMemo, useState } from "react"
import { isAxiosError } from "axios"
import { AlertTriangle, File, Loader2, Pencil, Plus, RefreshCw, Search, Trash2 } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { PaginationFooter } from "@/components/shared/PaginationFooter"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { pagesApi } from "@/lib/api"

type PageListItem = {
  id: number
  name?: string
  description?: string
  status?: string
  updated_at?: string
}

const PAGE_SIZE = 10

const STATUS_MAP: Record<string, { label: string; variant: "default" | "secondary" | "outline" }> = {
  draft: { label: "草稿", variant: "outline" },
  previewing: { label: "预览中", variant: "outline" },
  published: { label: "已发布", variant: "default" },
  archived: { label: "已归档", variant: "secondary" },
}

function nextPageName(existingPages: PageListItem[]): string {
  const numbers = existingPages
    .map((item) => String(item?.name || "").match(/^Page-(\d+)$/i))
    .filter((match): match is RegExpMatchArray => Boolean(match))
    .map((match) => Number(match[1]))
    .filter((num) => Number.isInteger(num) && num > 0)
  const next = numbers.length > 0 ? Math.max(...numbers) + 1 : existingPages.length + 1
  return `Page-${next}`
}

export function PageListPage() {
  const navigate = useNavigate()
  const [pages, setPages] = useState<PageListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [deleteTarget, setDeleteTarget] = useState<PageListItem | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)

  const fetchList = (showRefresh = false) => {
    if (showRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)
    pagesApi
      .list()
      .then((data) => setPages(Array.isArray(data) ? data : []))
      .catch(() => {
        if (!showRefresh) setError("加载 Page 列表失败")
        else toast.error("刷新失败")
      })
      .finally(() => {
        setLoading(false)
        setRefreshing(false)
      })
  }

  useEffect(() => {
    fetchList()
  }, [])

  const visiblePages = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return pages
    return pages.filter((item) =>
      `${item.id} ${item.name || ""} ${item.description || ""}`.toLowerCase().includes(keyword),
    )
  }, [pages, search])

  const pagedPages = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return visiblePages.slice(start, start + PAGE_SIZE)
  }, [visiblePages, page])

  useEffect(() => {
    setPage(1)
  }, [search])

  const handleCreate = async () => {
    if (creating) return
    setCreating(true)
    try {
      const name = nextPageName(pages)
      const created = await pagesApi.create({
        name,
        draft_payload: {
          version: "page-runtime-v2",
          config: { title: name, description: "" },
          source: { language: "tsx", code: "" },
          runtime: {
            framework: "html",
            preview_html:
              "<!doctype html><html><head><meta charset='utf-8' /></head><body style='margin:0;background:var(--color-background);font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;'><main style='max-width:960px;margin:0 auto;padding:24px;color:var(--color-muted-foreground);'>描述页面需求后，这里会显示实时结果。</main></body></html>",
          },
          meta: { updated_at: new Date().toISOString(), history: [], plan: { goal: "", todos: [] } },
        },
      })
      setPages((prev) => [created, ...prev])
      navigate(`/page/workspace/${created.id}`)
    } catch (err) {
      const detail = isAxiosError(err) ? String((err.response?.data as any)?.detail || "") : ""
      toast.error(detail || "创建 Page 失败")
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setBusyAction(`delete:${deleteTarget.id}`)
    try {
      await pagesApi.delete(deleteTarget.id)
      setPages((prev) => prev.filter((item) => item.id !== deleteTarget.id))
      setDeleteTarget(null)
      toast.success("Page 已删除")
    } catch (err) {
      const detail = isAxiosError(err) ? String((err.response?.data as any)?.detail || "") : ""
      toast.error(detail || "删除失败")
    } finally {
      setBusyAction(null)
    }
  }

  const handleRowClick = (item: PageListItem) => {
    const s = String(item.status || "").toLowerCase()
    if (s === "published") {
      navigate(`/page/${item.id}`)
    } else {
      navigate(`/page/workspace/${item.id}`)
    }
  }

  const toolbar = (
    <div className="rounded-xl bg-card p-4 shadow-sm">
      <FilterToolbar>
        <FilterToolbarGroup>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-72 rounded-lg bg-card pl-9 text-sm"
              placeholder="搜索名称或描述..."
            />
          </div>
        </FilterToolbarGroup>
        <FilterToolbarGroup>
          <Button variant="outline" size="sm" onClick={() => fetchList(true)} disabled={refreshing}>
            {refreshing ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            刷新
          </Button>
          <Button size="sm" onClick={handleCreate} disabled={creating}>
            {creating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            新建
          </Button>
        </FilterToolbarGroup>
      </FilterToolbar>
    </div>
  )

  const primary = (
    <section className="animate-in fade-in slide-in-from-bottom-1 duration-500">
      <div className="overflow-hidden rounded-xl bg-card shadow-sm">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-4">
            <Tabs value="all" onValueChange={() => {}} className="w-fit">
              <TabsList>
                <TabsTrigger value="all">
                  全部
                  {!loading && !error ? (
                    <span className="ml-1.5 rounded-full bg-muted px-1.5 text-[10px] tabular-nums">
                      {visiblePages.length}
                    </span>
                  ) : null}
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>
        <ListTable className="border-0 rounded-none">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[240px]">名称</TableHead>
                <TableHead className="w-24">状态</TableHead>
                <TableHead>描述</TableHead>
                <TableHead className="w-40">更新时间</TableHead>
                <TableHead className="w-24 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <ListTableLoadingRows rowCount={6} columnCount={5} />
              ) : error ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-32 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <AlertTriangle className="size-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">{error}</p>
                      <Button variant="ghost" size="sm" onClick={() => fetchList()}>
                        重试
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ) : pagedPages.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-32 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <File className="size-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">
                        {search ? "没有匹配的结果" : "暂无 Page"}
                      </p>
                      {search ? (
                        <Button variant="ghost" size="sm" onClick={() => setSearch("")}>
                          清除搜索
                        </Button>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={handleCreate} disabled={creating}>
                          <Plus className="size-4" />
                          新建 Page
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                pagedPages.map((item, index) => {
                  const status = STATUS_MAP[item.status || "draft"] || STATUS_MAP.draft
                  return (
                    <TableRow
                      key={item.id}
                      style={{ animationDelay: `${index * 30}ms` }}
                      className="cursor-pointer transition-colors duration-150 hover:bg-muted/40"
                      onClick={() => handleRowClick(item)}
                    >
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/50">
                            <File className="size-3.5 text-muted-foreground" />
                          </div>
                          <span className="font-medium text-foreground">
                            {item.name || `Page ${item.id}`}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={status.variant} className="text-[11px]">
                          {status.label}
                        </Badge>
                      </TableCell>
                      <TableCell
                        className="max-w-[400px] truncate text-muted-foreground"
                        title={item.description || "-"}
                      >
                        {item.description || "-"}
                      </TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">
                        {item.updated_at || "-"}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            onClick={(e) => {
                              e.stopPropagation()
                              navigate(`/page/workspace/${item.id}`)
                            }}
                            disabled={Boolean(busyAction)}
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            className="text-destructive hover:text-destructive"
                            onClick={(e) => {
                              e.stopPropagation()
                              setDeleteTarget(item)
                            }}
                            disabled={Boolean(busyAction)}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
          {!loading && !error ? (
            <PaginationFooter
              page={page}
              pageSize={PAGE_SIZE}
              total={visiblePages.length}
              onPageChange={setPage}
              className="border-t border-border px-4 py-2"
            />
          ) : null}
        </ListTable>
      </div>
    </section>
  )

  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />
      <ConfirmActionDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="删除 Page"
        description={
          <span className="space-y-1">
            <span className="block">
              将删除{" "}
              <span className="font-semibold text-foreground">
                {deleteTarget?.name || "当前 Page"}
              </span>{" "}
              及关联构建记录。操作不可恢复。
            </span>
            {deleteTarget ? (
              <span className="block text-xs text-muted-foreground">
                目标标识：#{deleteTarget.id}
              </span>
            ) : null}
          </span>
        }
        confirmText="删除"
        confirming={busyAction?.startsWith("delete:") ?? false}
        confirmDisabled={!deleteTarget}
        onConfirm={handleDelete}
      />
    </>
  )
}
