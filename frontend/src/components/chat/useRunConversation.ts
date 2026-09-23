import { useCallback, useEffect, useRef, useState } from "react"
import { agentRunsApi, applyRunEvent, consumeRunEvents, RunSubscriptionError, runView, terminalRun, type Reconciliation, type RunEvent, type RunScene, type RunView, type ToolBlock } from "@/lib/agentRuns"

export function useRunConversation(conversationId: string | null, onEvent?: (event: RunEvent, view: RunView) => void) {
  const eventListener = useRef(onEvent)
  useEffect(() => { eventListener.current = onEvent }, [onEvent])
  const [views, setViews] = useState<RunView[]>([])
  const [loading, setLoading] = useState(Boolean(conversationId))
  const [loadError, setLoadError] = useState(false)
  const [reload, setReload] = useState(0)
  const state = useRef(new Map<string, RunView>())
  const subscriptions = useRef(new Map<string, AbortController>())
  const frame = useRef<number | null>(null)
  const generation = useRef(0)
  const publish = useCallback((immediate = false) => {
    const update = () => { frame.current = null; setViews([...state.current.values()].sort((a, b) => a.run.seq - b.run.seq)) }
    if (immediate) { if (frame.current !== null) cancelAnimationFrame(frame.current); update() }
    else if (frame.current === null) frame.current = requestAnimationFrame(update)
  }, [])

  const subscribe = useCallback((id: string) => {
    if (subscriptions.current.has(id)) return
    const abort = new AbortController()
    const epoch = generation.current
    subscriptions.current.set(id, abort)
    const work = async () => {
      while (!abort.signal.aborted && epoch === generation.current) {
        try {
          const current = state.current.get(id)
          if (!current) return
          const response = await agentRunsApi.stream(id, current.cursor, abort.signal)
          await consumeRunEvents(response, event => {
            if (epoch !== generation.current || abort.signal.aborted) return
            const view = state.current.get(id)
            if (view) {
              const next = applyRunEvent(view, event)
              state.current.set(id, next); publish()
              if (next !== view) eventListener.current?.(event, next)
            }
          })
          const latest = state.current.get(id)
          if (latest?.terminalSeen && terminalRun(latest.run.status) && latest.cursor >= latest.run.event_seq) {
            state.current.set(id, { ...latest, connection: "closed" }); publish(); return
          }
        } catch (error) {
          if (abort.signal.aborted || epoch !== generation.current) return
          if (error instanceof RunSubscriptionError && [400, 401, 403, 404, 410, 422].includes(error.status)) {
            const view = state.current.get(id)
            if (view) { state.current.set(id, { ...view, connection: "unavailable", error: "subscription" }); publish() }
            return
          }
        }
        const view = state.current.get(id)
        if (view) { state.current.set(id, { ...view, connection: "reconnecting" }); publish() }
        await new Promise<void>(resolve => {
          const done = () => { clearTimeout(timer); abort.signal.removeEventListener("abort", done); resolve() }
          const timer = setTimeout(done, 1000)
          abort.signal.addEventListener("abort", done, { once: true })
        })
      }
    }
    void work().finally(() => { if (subscriptions.current.get(id) === abort) subscriptions.current.delete(id) })
  }, [publish])

  useEffect(() => {
    const epoch = ++generation.current
    const owners = subscriptions.current
    if (conversationId) {
      void agentRunsApi.runs(conversationId).then(runs => {
        if (epoch !== generation.current) return
        state.current.clear()
        for (const run of runs) { state.current.set(run.id, runView(run)); subscribe(run.id) }
        publish(true)
      }).catch(() => { if (epoch === generation.current) setLoadError(true) })
        .finally(() => { if (epoch === generation.current) setLoading(false) })
    }
    return () => {
      generation.current = epoch + 1
      for (const controller of owners.values()) controller.abort()
      owners.clear()
      if (frame.current !== null) cancelAnimationFrame(frame.current)
      frame.current = null
    }
  }, [conversationId, reload, publish, subscribe])

  const send = async (prompt: string, scene: RunScene, mode: "append" | "stop_and_modify" = "append", retryId?: string) => {
    if (!conversationId || !prompt.trim() || loading || loadError) return
    const epoch = generation.current
    const clientId = retryId ?? crypto.randomUUID()
    const localId = `local:${clientId}`
    const existing = state.current.get(localId)
    if (existing?.delivery === "sending") return
    const view = existing ?? runView({ id: localId, conversation_id: conversationId, prompt, seq: Math.max(0, ...[...state.current.values()].map(v => v.run.seq)) + 1, status: "queued", cancel_requested: false, event_seq: 0, error_code: null, output: null, created_at: Date.now() / 1000 })
    state.current.set(localId, { ...view, delivery: "sending", clientRequestId: clientId, submittedScene: scene, submittedMode: mode, error: undefined })
    publish(true)
    try {
      const run = await agentRunsApi.submit(conversationId, clientId, prompt, scene, mode)
      if (epoch !== generation.current) return
      state.current.delete(localId); state.current.set(run.id, { ...runView(run), clientRequestId: clientId }); publish(true); subscribe(run.id)
    } catch {
      if (epoch !== generation.current) return
      const current = state.current.get(localId)
      if (current) state.current.set(localId, { ...current, delivery: "failed" })
      publish(true)
    }
  }
  const cancel = async (id: string) => {
    const epoch = generation.current
    try {
      const run = await agentRunsApi.cancel(id)
      const view = state.current.get(id)
      if (view && epoch === generation.current) state.current.set(id, { ...view, run: run.event_seq >= view.cursor ? { ...view.run, ...run } : view.run, error: undefined })
    } catch {
      const view = state.current.get(id)
      if (view && epoch === generation.current) state.current.set(id, { ...view, error: "cancel" })
    }
    publish()
  }
  const decide = async (runId: string, call: ToolBlock, approved: boolean) => {
    if (!call.fingerprint || call.submitting || call.decision !== "pending") return
    const epoch = generation.current
    const patch = (fields: Partial<ToolBlock>) => {
      if (epoch !== generation.current) return
      const view = state.current.get(runId)
      if (view) state.current.set(runId, { ...view, blocks: view.blocks.map(block => block.kind === "tool" && block.id === call.id ? { ...block, ...fields } : block) })
      publish(true)
    }
    patch({ submitting: true, error: undefined })
    try {
      const result = await agentRunsApi.decide(runId, call.id, call.fingerprint, approved)
      patch({ decision: result.decision, submitting: false })
    } catch { patch({ submitting: false, error: "approval" }) }
  }
  const reconnect = (id: string) => {
    subscriptions.current.get(id)?.abort()
    subscriptions.current.delete(id)
    const view = state.current.get(id)
    if (view) state.current.set(id, { ...view, connection: "connecting", error: undefined })
    publish(true); subscribe(id)
  }
  const reconcile = async (runId: string, call: ToolBlock, check: Reconciliation) => {
    if (!call.fingerprint || call.submitting) return
    const epoch = generation.current
    const patch = (fields: Partial<ToolBlock>) => {
      const view = state.current.get(runId)
      if (!view || epoch !== generation.current) return
      state.current.set(runId, { ...view, blocks: view.blocks.map(block => block.kind === "tool" && block.id === call.id ? { ...block, ...fields } : block) }); publish(true)
    }
    patch({ submitting: true, error: undefined })
    try {
      await agentRunsApi.reconcile(runId, call.id, call.fingerprint, check)
      if (epoch !== generation.current) return
      patch({ submitting: false }); reconnect(runId)
    } catch { patch({ submitting: false, error: "reconciliation" }) }
  }
  const resume = async (id: string) => {
    const view = state.current.get(id)
    if (!view || view.resuming) return
    const epoch = generation.current
    state.current.set(id, { ...view, resuming: true, error: undefined }); publish(true)
    try {
      const run = await agentRunsApi.resume(id, view.run.event_seq)
      if (epoch !== generation.current) return
      const latest = state.current.get(id)
      if (latest) state.current.set(id, { ...latest, run: run.event_seq >= latest.cursor ? { ...latest.run, ...run } : latest.run, resuming: false })
      reconnect(id)
    } catch {
      const latest = state.current.get(id)
      if (latest && epoch === generation.current) { state.current.set(id, { ...latest, resuming: false, error: "resume" }); publish(true) }
    }
  }
  return { views, loading, loadError, refresh: () => { setLoading(true); setLoadError(false); setReload(v => v + 1) }, send, cancel, decide, reconcile, resume, reconnect }
}
