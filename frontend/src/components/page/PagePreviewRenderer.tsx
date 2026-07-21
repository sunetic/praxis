import { ensurePagePreviewTheme } from "@/lib/pageTheme"

type PageDraft = {
  runtime?: {
    preview_html?: string
  }
}

type PagePreviewRendererProps = {
  draft: PageDraft
  canvasWidth?: number
  canvasHeight?: number
  scalePercent?: number
  className?: string
}

function clampScalePercent(value: number | undefined): number {
  if (!Number.isFinite(value)) return 85
  return Math.max(50, Math.min(140, Math.round(Number(value))))
}

function isPlaceholderPreviewHtml(html: string): boolean {
  const normalized = html.trim()
  if (!normalized) return true
  const markers = [
    "描述页面需求后，这里会显示实时结果。",
    "preview canvas is ready",
    "describe your page idea in build chat",
  ]
  const lower = normalized.toLowerCase()
  return markers.some((marker) => lower.includes(marker.toLowerCase()))
}

export function PagePreviewRenderer({
  draft,
  canvasWidth = 1366,
  canvasHeight = 900,
  scalePercent = 85,
  className,
}: PagePreviewRendererProps) {
  const rawPreviewHtml = typeof draft?.runtime?.preview_html === "string" ? draft.runtime.preview_html.trim() : ""
  const previewHtml = ensurePagePreviewTheme(rawPreviewHtml)
  const showPlaceholder = isPlaceholderPreviewHtml(previewHtml)
  const normalizedScale = clampScalePercent(scalePercent)
  const scale = normalizedScale / 100
  const scaledWidth = Math.round(canvasWidth * scale)
  const scaledHeight = Math.round(canvasHeight * scale)

  if (showPlaceholder) {
    return (
      <div
        className={`flex h-full min-h-[24rem] items-center justify-center rounded-xl border border-border bg-card text-sm text-muted-foreground ${className || ""}`}
      >
        <div className="flex w-full max-w-xl flex-col items-center justify-center gap-4 px-6 py-10 text-center">
          <div className="relative flex h-16 w-16 items-center justify-center rounded-xl border border-border bg-muted/70 shadow-sm">
            <div className="h-2 w-2 rounded-full bg-primary/90" />
            <div className="absolute -left-1 top-2 h-2 w-2 rounded-full bg-primary/40" />
            <div className="absolute -right-1 bottom-2 h-2 w-2 rounded-full bg-primary/50" />
          </div>
          <div className="space-y-1">
            <p className="text-base font-semibold text-foreground">Preview Canvas Is Ready</p>
            <p className="text-sm text-muted-foreground">
              Describe your page idea in Build Chat. The preview will appear here with smooth updates.
            </p>
          </div>
          <div className="grid w-full max-w-sm grid-cols-3 gap-2 pt-2">
            {Array.from({ length: 6 }).map((_, idx) => (
              <div
                key={idx}
                className="h-3 rounded-lg bg-muted/70"
                style={{ opacity: 0.9 - idx * 0.1 }}
              />
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`flex h-full w-full items-start justify-center overflow-auto p-4 ${className || ""}`}>
      <div
        className="relative shrink-0 overflow-hidden rounded-xl border border-border bg-card shadow-md"
        style={{ width: `${scaledWidth}px`, height: `${scaledHeight}px` }}
      >
        <iframe
          title="Page Runtime Preview"
          className="absolute left-0 top-0 border-0 bg-card"
          style={{
            width: `${canvasWidth}px`,
            height: `${canvasHeight}px`,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
          }}
          sandbox="allow-scripts allow-forms allow-modals allow-popups"
          srcDoc={previewHtml}
        />
      </div>
    </div>
  )
}
