import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { ArrowLeft, BookOpen, ChevronDown, ChevronRight, Eye, FileText, Folder, FolderUp, Loader2, Trash2, Upload } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { FilterToolbar, FilterToolbarGroup } from "@/components/shared/FilterToolbar"
import { ListTable, ListTableLoadingRows } from "@/components/shared/ListTable"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { useShellI18n } from "@/i18n/shellI18n"
import { knowledgeApi } from "@/lib/api"
import type { KnowledgeBase, KnowledgeDocument, KnowledgeDocumentDetail } from "@/lib/api"

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message
  }
  return fallback
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

type TreeNode = {
  name: string
  path: string
  children: TreeNode[]
  docs: KnowledgeDocument[]
}

function buildTree(documents: KnowledgeDocument[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: [], docs: [] }

  for (const doc of documents) {
    const parts = doc.filename.split("/")
    let node = root
    for (let i = 0; i < parts.length - 1; i++) {
      const dirName = parts[i]
      const dirPath = parts.slice(0, i + 1).join("/")
      let child = node.children.find((c) => c.name === dirName)
      if (!child) {
        child = { name: dirName, path: dirPath, children: [], docs: [] }
        node.children.push(child)
      }
      node = child
    }
    node.docs.push(doc)
  }

  const sortNode = (node: TreeNode) => {
    node.children.sort((a, b) => a.name.localeCompare(b.name))
    node.docs.sort((a, b) => a.filename.localeCompare(b.filename))
    node.children.forEach(sortNode)
  }
  sortNode(root)
  return root
}

function countDocs(node: TreeNode): number {
  return node.docs.length + node.children.reduce((sum, c) => sum + countDocs(c), 0)
}

export function KnowledgeDetailPage() {
  const { kbId } = useParams<{ kbId: string }>()
  const navigate = useNavigate()
  const { t } = useShellI18n()
  const numKbId = Number(kbId)

  const [kb, setKb] = useState<KnowledgeBase | null>(null)
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  const [viewDoc, setViewDoc] = useState<KnowledgeDocumentDetail | null>(null)
  const [viewLoading, setViewLoading] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocument | null>(null)
  const [deleting, setDeleting] = useState(false)

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const fetchData = useCallback(async () => {
    if (!numKbId) return
    setLoading(true)
    setError(null)
    try {
      const [kbData, docs] = await Promise.all([
        knowledgeApi.get(numKbId),
        knowledgeApi.listDocuments(numKbId),
      ])
      setKb(kbData)
      setDocuments(docs)
    } catch (e) {
      setError(getErrorMessage(e, t("knowledgeDetail.loadFailed")))
    } finally {
      setLoading(false)
    }
  }, [numKbId, t])

  useEffect(() => { fetchData() }, [fetchData])

  const tree = useMemo(() => buildTree(documents), [documents])

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (!files || files.length === 0) return

    const isFolder = e.target === folderInputRef.current
    const allFiles = Array.from(files)
    const mdFiles = allFiles.filter((f) => f.name.endsWith(".md"))

    if (!isFolder) {
      const skipped = allFiles.length - mdFiles.length
      if (skipped > 0) toast.error(`${skipped} ${t("knowledgeDetail.skippedFiles")}`)
    }

    if (mdFiles.length === 0) {
      if (isFolder) toast.error(t("knowledgeDetail.noMdInFolder"))
      return
    }

    setUploading(true)
    let successCount = 0
    let failCount = 0

    const BATCH = 5
    for (let i = 0; i < mdFiles.length; i += BATCH) {
      const batch = mdFiles.slice(i, i + BATCH)
      const results = await Promise.allSettled(
        batch.map((file) => knowledgeApi.uploadDocument(numKbId, file)),
      )
      for (let j = 0; j < results.length; j++) {
        if (results[j].status === "fulfilled") {
          successCount++
        } else {
          const err = (results[j] as PromiseRejectedResult).reason
          toast.error(`${batch[j].name}: ${getErrorMessage(err, t("knowledgeDetail.uploadFailed"))}`)
          failCount++
        }
      }
    }

    if (successCount > 0) {
      const msg = t("knowledgeDetail.uploadSuccess").replace("{count}", String(successCount))
        + (failCount > 0 ? t("knowledgeDetail.uploadPartial").replace("{count}", String(failCount)) : "")
      toast.success(msg)
      fetchData()
    }
    setUploading(false)
    if (fileInputRef.current) fileInputRef.current.value = ""
    if (folderInputRef.current) folderInputRef.current.value = ""
  }

  async function handleViewDoc(doc: KnowledgeDocument) {
    setViewLoading(true)
    try {
      const detail = await knowledgeApi.getDocument(numKbId, doc.id)
      setViewDoc(detail)
    } catch (e) {
      toast.error(getErrorMessage(e, t("knowledgeDetail.loadDocFailed")))
    } finally {
      setViewLoading(false)
    }
  }

  async function handleDeleteDoc() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await knowledgeApi.deleteDocument(numKbId, deleteTarget.id)
      toast.success(t("knowledgeDetail.toast.docDeleted"))
      setDeleteTarget(null)
      fetchData()
    } catch (e) {
      toast.error(getErrorMessage(e, t("knowledgeDetail.deleteFailed")))
    } finally {
      setDeleting(false)
    }
  }

  function toggleFolder(path: string) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const columnCount = 4

  const toolbar = (
    <FilterToolbar>
      <FilterToolbarGroup>
        <Button variant="ghost" size="sm" onClick={() => navigate("/knowledge")}>
          <ArrowLeft className="size-4" />
          {t("knowledgeDetail.backToList")}
        </Button>
        {kb && (
          <span className="text-sm font-medium">{kb.name}</span>
        )}
      </FilterToolbarGroup>
      <FilterToolbarGroup>
        <input
          ref={fileInputRef}
          type="file"
          accept=".md"
          multiple
          className="hidden"
          onChange={handleUpload}
        />
        <input
          ref={folderInputRef}
          type="file"
          className="hidden"
          onChange={handleUpload}
          {...{ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => folderInputRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? <Loader2 className="size-4 animate-spin" /> : <FolderUp className="size-4" />}
          {t("knowledgeDetail.btn.uploadFolder")}
        </Button>
        <Button
          size="sm"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
          {t("knowledgeDetail.btn.uploadDoc")}
        </Button>
      </FilterToolbarGroup>
    </FilterToolbar>
  )

  function renderDocRow(doc: KnowledgeDocument, depth: number) {
    const fileName = doc.filename.split("/").pop() || doc.filename
    return (
      <TableRow
        key={doc.id}
        className="cursor-pointer transition-colors duration-150 hover:bg-muted/40"
        onClick={() => handleViewDoc(doc)}
      >
        <TableCell>
          <div className="flex items-center gap-2" style={{ paddingLeft: `${depth * 20 + 4}px` }}>
            <div className="flex size-6 shrink-0 items-center justify-center rounded bg-muted/50">
              <FileText className="size-3.5 text-muted-foreground" />
            </div>
            <span className="font-medium text-sm">{fileName}</span>
          </div>
        </TableCell>
        <TableCell className="text-muted-foreground tabular-nums text-sm">
          {formatBytes(doc.size_bytes)}
        </TableCell>
        <TableCell className="font-mono text-xs text-muted-foreground">
          {doc.id}
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-0.5" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => handleViewDoc(doc)}
              disabled={viewLoading}
            >
              <Eye className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-destructive hover:text-destructive"
              onClick={() => setDeleteTarget(doc)}
            >
              <Trash2 className="size-3.5" />
            </Button>
          </div>
        </TableCell>
      </TableRow>
    )
  }

  function renderTreeNode(node: TreeNode, depth: number): React.ReactNode[] {
    const rows: React.ReactNode[] = []
    const isCollapsed = collapsed.has(node.path)

    for (const child of node.children) {
      const docCount = countDocs(child)
      const childCollapsed = collapsed.has(child.path)
      rows.push(
        <TableRow
          key={`dir-${child.path}`}
          className="cursor-pointer transition-colors duration-150 hover:bg-muted/40 bg-muted/20"
          onClick={() => toggleFolder(child.path)}
        >
          <TableCell>
            <div className="flex items-center gap-2" style={{ paddingLeft: `${depth * 20 + 4}px` }}>
              {childCollapsed
                ? <ChevronRight className="size-4 text-muted-foreground shrink-0" />
                : <ChevronDown className="size-4 text-muted-foreground shrink-0" />
              }
              <Folder className="size-4 text-muted-foreground shrink-0" />
              <span className="font-medium text-sm">{child.name}</span>
              <span className="text-xs text-muted-foreground tabular-nums">{docCount}</span>
            </div>
          </TableCell>
          <TableCell />
          <TableCell />
          <TableCell />
        </TableRow>,
      )
      if (!childCollapsed) {
        rows.push(...renderTreeNode(child, depth + 1))
      }
    }

    if (!isCollapsed) {
      for (const doc of node.docs) {
        rows.push(renderDocRow(doc, depth))
      }
    }

    return rows
  }

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

    if (documents.length === 0) {
      return (
        <TableRow>
          <TableCell colSpan={columnCount} className="h-32 text-center">
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <FileText className="size-5" />
              <span className="text-sm">{t("knowledgeDetail.empty")}</span>
            </div>
          </TableCell>
        </TableRow>
      )
    }

    return renderTreeNode(tree, 0)
  }

  const primary = (
    <div className="rounded-xl bg-card shadow-sm">
      <div className="flex items-center gap-4 px-4 pt-4">
        <Tabs value="docs" onValueChange={() => {}} className="w-fit">
          <TabsList>
            <TabsTrigger value="docs">{t("knowledgeDetail.tab.docs")}</TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="text-xs tabular-nums text-muted-foreground">
          {documents.length} {t("knowledgeDetail.docCount")}
        </span>
      </div>
      <ListTable className="overflow-hidden border-0 rounded-none">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>{t("knowledgeDetail.col.name")}</TableHead>
              <TableHead>{t("knowledgeDetail.col.size")}</TableHead>
              <TableHead>{t("knowledgeDetail.col.id")}</TableHead>
              <TableHead className="text-right">{t("knowledgeDetail.col.actions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>{renderTableBody()}</TableBody>
        </Table>
      </ListTable>
    </div>
  )

  return (
    <>
      <WorkbenchPage toolbar={toolbar} primary={primary} />

      <Dialog open={!!viewDoc} onOpenChange={(open) => !open && setViewDoc(null)}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>{viewDoc?.title}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto rounded-lg border border-border bg-muted/10 p-4">
            <pre className="whitespace-pre-wrap text-sm leading-relaxed font-mono text-foreground">
              {viewDoc?.content}
            </pre>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmActionDialog
        open={!!deleteTarget}
        title={t("knowledgeDetail.delete.title")}
        description={t("knowledgeDetail.delete.desc").replace("{name}", deleteTarget?.title ?? "")}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        onConfirm={handleDeleteDoc}
        confirming={deleting}
      />
    </>
  )
}
