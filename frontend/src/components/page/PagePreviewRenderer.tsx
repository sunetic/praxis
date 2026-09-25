import { useEffect, useRef } from "react"
import { useShellI18n } from "@/i18n/shellI18nContext"

/** Untrusted compiled HTML has an opaque origin; never grant same-origin or top navigation. */
export function PagePreviewRenderer({ html, title }: { html: string; title: string }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const { t } = useShellI18n()
  useEffect(() => {
    const unavailable = (event: MessageEvent) => {
      const source = frame.current?.contentWindow
      const data = event.data
      if (!source || event.source !== source || !data || data.type !== "praxis.page.invoke" || typeof data.id !== "string" || data.id.length > 128) return
      // No host API fallback. Fail explicitly until version-pinned invocation is connected.
      source.postMessage({ type: "praxis.page.result", id: data.id, error: t("page.bindingUnavailable") }, "*")
    }
    window.addEventListener("message", unavailable)
    return () => window.removeEventListener("message", unavailable)
  }, [t])
  return <iframe ref={frame} title={title} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={html} className="h-[65dvh] min-h-96 w-full border border-border bg-background" />
}
