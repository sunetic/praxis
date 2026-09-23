/** Test-only HTTP fixture. Real browser + incremental SSE, no claim of LLM quality. */
import { createServer, type ServerResponse } from "node:http"
import { readFile } from "node:fs/promises"
import { resolve, extname, sep } from "node:path"
import type { AgentRun, RunEvent, RunScene } from "../src/lib/agentRuns"
import type { FunctionDraft } from "../src/lib/functionArtifacts"
import type { PageDraft } from "../src/lib/pageArtifacts"

export async function replayServer(initialDraft?: FunctionDraft, initialPage?: PageDraft, initialScene?: RunScene) {
  let draft = initialDraft
  let pageDraft = initialPage
  let pageHtml = "<main>Compiled preview fixture</main>"
  const conversation = { id: "browser-conversation", title: "浏览器回放", scene: initialScene ?? (pageDraft ? { page_ids: [pageDraft.page_id], datasource_ids: [] } : draft ? { function_ids: [draft.function_id], datasource_ids: [] } : {}) as RunScene, created_at: 1, active_run_id: null }
  const runs: AgentRun[] = []
  const events: RunEvent[] = []
  const clients = new Set<ServerResponse>()
  const cursors: number[] = []
  const submissions: Record<string, unknown>[] = []
  const decisions: Record<string, unknown>[] = []
  let acknowledge: (() => void) | undefined
  let holdSubmission = false
  let cancellations = 0
  const disconnect = () => {
    const closing = [...clients]
    clients.clear()
    for (const client of closing) client.end()
  }
  const frame = (event: RunEvent) => `id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`
  const send = (type: string, fields: Partial<RunEvent> = {}) => {
    const run = runs[0]
    if (!run) throw new Error("Submit before sending fixture events")
    const event: RunEvent = { run_id: run.id, seq: events.length + 1, type, ...fields }
    events.push(event)
    run.event_seq = event.seq
    if (fields.status && type.startsWith("run_")) run.status = fields.status as AgentRun["status"]
    for (const client of clients) client.write(frame(event))
    if (["run_finished", "run_cancelled", "run_failed", "run_limited", "run_interrupted"].includes(type)) disconnect()
  }
  const root = resolve("dist")
  const server = createServer(async (request, response) => {
    try {
      const pathname = new URL(request.url!, "http://fixture").pathname
      const json = (body: unknown, status = 200) => { response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(body)) }
      if (pageDraft && pathname === `/api/v1/pages/${pageDraft.page_id}/draft`) return json(pageDraft)
      if (pageDraft && pathname === `/api/v1/pages/${pageDraft.page_id}`) return json({ id: pageDraft.page_id, name: pageDraft.name })
      if (pageDraft && pathname === `/api/v1/pages/${pageDraft.page_id}/preview`) return json({ page_id: pageDraft.page_id, revision_id: pageDraft.revision_id, artifact_hash: pageDraft.artifact_hash, validation_id: pageDraft.validation?.id, html: pageHtml })
      if (draft && pathname === `/api/v1/functions/${draft.function_id}/draft`) return json(draft)
      if (draft && pathname === `/api/v1/functions/${draft.function_id}`) return json({ id: draft.function_id, name: draft.name, kind: "custom" })
      if (pathname.endsWith("/events")) {
        const after = Number(request.headers["last-event-id"] ?? 0)
        cursors.push(after)
        response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" })
        response.flushHeaders()
        for (const event of events) if (event.seq > after) response.write(frame(event))
        if (["finished", "cancelled", "failed", "limited", "interrupted"].includes(runs[0]?.status)) response.end()
        else { clients.add(response); response.on("close", () => clients.delete(response)) }
        return
      }
      let raw = ""
      for await (const chunk of request) raw += chunk
      const body = raw ? JSON.parse(raw) : {}
      if (pathname === "/api/v1/conversations") return json([conversation])
      if (pathname === `/api/v1/conversations/${conversation.id}` && request.method === "PATCH") {
        conversation.scene = body.scene; return json(conversation)
      }
      if (pathname.endsWith("/runs") && request.method === "POST") {
        submissions.push(body)
        const run: AgentRun = { id: `browser-run-${runs.length + 1}`, conversation_id: conversation.id, prompt: body.prompt, seq: runs.length + 1, status: "queued", cancel_requested: false, output: null, error_code: null, event_seq: 0, created_at: Date.now() / 1000 }
        runs.push(run)
        acknowledge = () => json(run, 202)
        if (!holdSubmission) acknowledge()
        return
      }
      if (pathname.endsWith("/runs")) return json(runs)
      if (pathname.endsWith("/approval")) { decisions.push({ ...body, path: pathname }); return json({ decision: body.approved ? "approved" : "denied" }) }
      if (pathname.endsWith("/cancel")) { cancellations++; runs[0].cancel_requested = true; return json(runs[0]) }
      if (pathname === "/api/v1/onboarding/status") return json({ completed: true })
      if (pathname === "/api/v1/datasources") return json([{ id: 1, name: "回放数据库", status: "active", db_type: "postgresql" }])
      if (pathname.startsWith("/api/")) return json([])
      const asset = pathname.startsWith("/assets/") ? resolve(root, `.${pathname}`) : resolve(root, "index.html")
      if (!asset.startsWith(root + sep)) { response.writeHead(403); response.end(); return }
      const types: Record<string, string> = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".woff2": "font/woff2", ".woff": "font/woff" }
      response.writeHead(200, { "Content-Type": types[extname(asset)] ?? "application/octet-stream" })
      response.end(await readFile(asset))
    } catch { response.writeHead(500); response.end("Fixture error") }
  })
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve))
  const address = server.address()
  if (!address || typeof address === "string") throw new Error("No fixture port")
  return {
    url: `http://127.0.0.1:${address.port}`, runs, events, submissions, decisions, cursors,
    get scene() { return conversation.scene },
    setDraft: (value: FunctionDraft) => { draft = value },
    setPageDraft: (value: PageDraft, html = pageHtml) => { pageDraft = value; pageHtml = html },
    send, hold: () => { holdSubmission = true }, acknowledge: () => acknowledge?.(),
    disconnect,
    get cancellations() { return cancellations },
    close: async () => { server.closeAllConnections(); await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())) },
  }
}
