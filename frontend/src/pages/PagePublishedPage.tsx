import { useEffect, useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { Loader2, Pencil } from "lucide-react"

import { Button } from "@/components/ui/button"
import { pagesApi } from "@/lib/api"
import { ensurePagePreviewTheme } from "@/lib/pageTheme"

export function PagePublishedPage() {
  const { pageId } = useParams()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [published, setPublished] = useState<any | null>(null)

  const numericPageId = Number(pageId)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    pagesApi
      .getPublished(numericPageId)
      .then((data) => {
        if (!cancelled) setPublished(data)
      })
      .catch(() => {
        if (!cancelled) setPublished(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [numericPageId])

  const previewHtml = useMemo(
    () => {
      const payload = published?.release?.artifact_payload
      if (!payload || typeof payload !== "object") return ""
      if (payload.kind === "runtime_page" && payload.runtime && typeof payload.runtime === "object") {
        return ensurePagePreviewTheme(String(payload.runtime.preview_html || ""))
      }
      return ""
    },
    [published]
  )

  if (loading) {
    return (
      <div className="flex min-h-[calc(100vh-8rem)] items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        加载发布页面...
      </div>
    )
  }

  if (!published) {
    return (
      <div className="flex min-h-[calc(100vh-8rem)] items-center justify-center text-muted-foreground">
        页面未发布或不存在
      </div>
    )
  }

  return (
    <div className="relative h-[calc(100vh-8rem)] w-full overflow-hidden">
      <div className="absolute right-3 top-3 z-20">
        <Button
          size="sm"
          variant="secondary"
          onClick={() => navigate(`/page/workspace/${numericPageId}?from=published`)}
        >
          <Pencil className="mr-1.5 size-3.5" />
          编辑
        </Button>
      </div>
      {previewHtml ? (
        <iframe
          title="Published Runtime Page"
          className="h-full w-full border-0 bg-transparent"
          sandbox="allow-scripts allow-forms allow-modals allow-popups"
          srcDoc={previewHtml}
        />
      ) : (
        <div className="flex h-full items-center justify-center text-muted-foreground">发布内容不可用</div>
      )}
    </div>
  )
}
