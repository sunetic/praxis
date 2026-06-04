const PREVIEW_THEME_CSS = `
:root{
  --background:#f8f9fa;
  --foreground:#1a1a1a;
  --card:#ffffff;
  --muted:#f1f3f5;
  --muted-foreground:#868e96;
  --primary:#6366f1;
  --primary-foreground:#ffffff;
  --border:#e9ecef;
  --radius:12px;
}
*{box-sizing:border-box}
html,body{width:100%;min-height:100%}
body{
  margin:0;
  background:var(--background);
  color:var(--foreground);
  font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
  line-height:1.5;
}
main{
  max-width:1120px;
  margin:0 auto;
  padding:24px;
}
.card{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:var(--radius);
  box-shadow:0 1px 2px rgba(15,23,42,.04);
}
button,.btn{
  border:1px solid transparent;
  border-radius:10px;
  padding:10px 14px;
  background:var(--primary);
  color:var(--primary-foreground);
  font:inherit;
}
input,select,textarea{
  width:100%;
  border:1px solid var(--border);
  border-radius:10px;
  padding:10px 12px;
  background:#fff;
  color:var(--foreground);
}
table{
  width:100%;
  border-collapse:collapse;
  background:#fff;
}
th,td{
  border-bottom:1px solid var(--border);
  text-align:left;
  padding:10px 12px;
}
th{
  font-weight:600;
  background:var(--muted);
}
`.trim()

function buildDefaultPreviewHtml(message?: string): string {
  const rawNote = String(message ?? "").trim()
  const note = rawNote
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
  const noteBlock = note
    ? "<section class='card' style='padding:16px;color:var(--muted-foreground);'>" + note + "</section>"
    : ""
  return (
    "<!doctype html><html><head><meta charset='utf-8' />" +
    `<style id='praxis-preview-theme'>${PREVIEW_THEME_CSS}</style>` +
    "</head><body><main>" +
    noteBlock +
    "</main></body></html>"
  )
}

export function ensurePagePreviewTheme(html: string): string {
  const normalized = String(html || "").trim()
  if (!normalized) return buildDefaultPreviewHtml()
  if (!normalized.toLowerCase().includes("<html")) {
    const escaped = normalized
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
    return buildDefaultPreviewHtml(escaped)
  }
  let patched = normalized
  if (!/<head\b/i.test(patched)) {
    patched = patched.replace(/<html([^>]*)>/i, "<html$1><head></head>")
  }
  if (!/<meta[^>]+charset/i.test(patched)) {
    patched = patched.replace(/<head([^>]*)>/i, "<head$1><meta charset='utf-8' />")
  }
  if (!patched.toLowerCase().includes("praxis-preview-theme")) {
    patched = patched.replace(
      /<\/head\s*>/i,
      `<style id='praxis-preview-theme'>${PREVIEW_THEME_CSS}</style></head>`
    )
  }
  return patched
}
