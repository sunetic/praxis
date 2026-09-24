import { useEffect, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { PagePreviewRenderer } from "@/components/page/PagePreviewRenderer"
import { useShellI18n } from "@/i18n/shellI18nContext"
import { pageArtifactsApi, type PublishedPage } from "@/lib/pageArtifacts"

export function PagePublishedPage() {
  const { pageId } = useParams()
  return <Published key={pageId} id={Number(pageId)} />
}

function Published({ id }: { id: number }) {
  const { t } = useShellI18n()
  const [record, setRecord] = useState<PublishedPage | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    void pageArtifactsApi.published(id).then(value => { if (active) setRecord(value) })
      .catch(() => { if (active) setRecord(null) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id])
  if (loading) return <p role="status">{t("runtime.loading")}</p>
  if (!record?.release.artifact_payload.html) return <p role="alert">{t("page.publishedUnavailable")}</p>
  const payload = record.release.artifact_payload
  return <section className="min-w-0 space-y-4">
    <header className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-xl font-semibold">{record.page.name}</h1><Link to={`/page/workspace/${id}`} className="inline-flex min-h-11 items-center underline">{t("page.editPublished")}</Link></header>
    {Object.keys(payload.bindings).length > 0 && <p role="status" className="text-sm text-muted-foreground">{t("page.bindingUnavailable")}</p>}
    <PagePreviewRenderer key={payload.artifact_hash} html={payload.html} title={t("page.published")} />
  </section>
}
