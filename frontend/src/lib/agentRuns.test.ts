import { describe, expect, it } from "vitest"
import { applyRunEvent, consumeRunEvents, runView, type AgentRun, type RunEvent } from "./agentRuns"

export const sampleRun = (patch: Partial<AgentRun> = {}): AgentRun => ({ id: "run-1", conversation_id: "conversation-1", prompt: "Read the sample", seq: 1, status: "queued", cancel_requested: false, output: null, error_code: null, event_seq: 0, created_at: 1, ...patch })
export const event = (seq: number, type: string, patch: Partial<RunEvent> = {}): RunEvent => ({ run_id: "run-1", seq, type, ...patch })
export const sse = (events: RunEvent[]) => new Response(events.map(item => `id: ${item.seq}\nevent: ${item.type}\ndata: ${JSON.stringify(item)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } })

describe("native run projection", () => {
  it("projects context work as timed status, never as assistant text", () => {
    let view = applyRunEvent(runView(sampleRun()), event(1, "context_status", { context_window_tokens: 1000, estimated_tokens: 700, used_percent: 70, compression_threshold_percent: 75, compression_threshold_tokens: 750, remaining_tokens: 300, token_source: "estimate", state: "ready" }))
    expect(view.contextStatus).toMatchObject({ used_percent: 70, state: "ready" })
    view = applyRunEvent(view, event(2, "context_compaction_started", { created_at: 123, context_window_tokens: 1000, before_context_tokens: 760, compression_threshold_percent: 75 }))
    expect(view.activity).toBe("context")
    expect(view.activityAt).toBe(123_000)
    expect(view.contextStatus).toMatchObject({ used_percent: 76, state: "compressing" })
    expect(view.blocks).toEqual([])
    view = applyRunEvent(view, event(3, "context_compacted", { context_window_tokens: 1000, before_context_tokens: 760, after_context_tokens: 320, source_indices: [0, 1, 2, 3] }))
    expect(view.activity).toBe("queued")
    expect(view.contextStatus).toMatchObject({ used_percent: 32, state: "ready" })
    expect(view.contextCompressionNotice).toEqual({ before_percent: 76, after_percent: 32, compacted_message_count: 4 })
    view = applyRunEvent(view, event(4, "request_started", { created_at: 130 }))
    expect(view.activity).toBe("model")
    expect(view.activityAt).toBe(130_000)
    expect(view.blocks).toEqual([])
  })
  it("restores waiting time from persisted timestamps rather than starting it over on replay", () => {
    const view = applyRunEvent(runView(sampleRun({ created_at: 123 })), event(1, "request_started", { created_at: 125 }))
    expect(view.activityAt).toBe(125_000)
  })
  it("appends by message/part identity, interleaves tools, and ignores replayed events", () => {
    let view = runView(sampleRun())
    const events = [
      event(1, "request_started"), event(2, "assistant_delta", { message_id: "m1", part_id: "0", text: "先看。" }),
      event(3, "assistant_message_end", { message_id: "m1" }), event(4, "tool_start", { call_id: "c1", name: "query_database" }),
      event(5, "tool_result", { call_id: "c1", status: "succeeded", content: { rows: [7] } }),
      event(6, "assistant_delta", { message_id: "m2", part_id: "0", text: "结果" }),
      event(7, "assistant_delta", { message_id: "m2", part_id: "0", text: "是 7。" }),
      event(8, "run_finished", { status: "finished" }),
    ]
    for (const item of events) view = applyRunEvent(view, item)
    expect(view.blocks.map(block => block.kind)).toEqual(["text", "tool", "text"])
    expect(view.blocks[2]).toMatchObject({ text: "结果是 7。" })
    expect(view.run.status).toBe("finished")
    expect(view.terminalSeen).toBe(true)
    expect(applyRunEvent(view, events[6])).toBe(view)
    expect(view.blocks.filter(block => block.kind === "text")).toHaveLength(2)
  })
  it("removes a provider response discarded for textual tool-call markup", () => {
    let view = applyRunEvent(runView(sampleRun()), event(1, "assistant_delta", { message_id: "bad", part_id: "0", text: "我来处理。" }))
    view = applyRunEvent(view, event(2, "assistant_message_discarded", { message_id: "bad" }))
    view = applyRunEvent(view, event(3, "assistant_delta", { message_id: "retry", part_id: "0", text: "请确认数据库变更。" }))
    expect(view.blocks).toHaveLength(1)
    expect(view.blocks[0]).toMatchObject({ messageId: "retry", text: "请确认数据库变更。" })
  })
  it("approval only changes the decision, not execution success", () => {
    let view = applyRunEvent(runView(sampleRun()), event(1, "approval_required", { call_id: "c1", name: "write", fingerprint: "f", arguments: { value: 1 } }))
    view = applyRunEvent(view, event(2, "approval_decided", { call_id: "c1", decision: "approved" }))
    expect(view.blocks[0]).toMatchObject({ status: "waiting_approval", decision: "approved", fingerprint: "f" })
    view = applyRunEvent(view, event(3, "tool_result", { call_id: "c1", outcome: "interrupted", content: "unknown" }))
    expect(view.blocks[0]).toMatchObject({ status: "outcome_unknown" })
  })
  it("projects automatic approval as an audited tool fact", () => {
    let view = applyRunEvent(runView(sampleRun()), event(1, "approval_decided", { call_id: "c1", decision: "approved", automatic: true }))
    view = applyRunEvent(view, event(2, "tool_start", { call_id: "c1", name: "request_database_change" }))
    expect(view.blocks[0]).toMatchObject({ status: "executing", decision: "approved", autoApproved: true })
  })
  it("keeps the original call during explicit reconciliation and reopens the same run", () => {
    let view = applyRunEvent(runView(sampleRun()), event(1, "tool_start", { call_id: "c1", name: "write", fingerprint: "f" }))
    view = applyRunEvent(view, event(2, "run_interrupted", { status: "interrupted" }))
    expect(view.blocks[0]).toMatchObject({ status: "outcome_unknown", fingerprint: "f" })
    view = applyRunEvent(view, event(3, "tool_reconciled", { call_id: "c1", status: "succeeded", content: { reconciliation: { verified_by_platform: false } } }))
    expect(view.blocks).toHaveLength(1)
    expect(view.blocks[0]).toMatchObject({ status: "succeeded", reconciled: true })
    expect(view.run.status).toBe("interrupted")
    view = applyRunEvent(view, event(4, "run_resumed", { status: "queued" }))
    expect(view.terminalSeen).toBe(false)
    expect(view.run.status).toBe("queued")
  })
  it("does not accept an event gap or confuse an already-finished snapshot with replay completion", () => {
    const view = runView(sampleRun({ status: "finished", event_seq: 20 }))
    expect(() => applyRunEvent(view, event(2, "run_finished", { status: "finished" }))).toThrow(/gap/)
    const updated = applyRunEvent(view, event(1, "run_queued"))
    expect(updated.run.event_seq).toBe(20)
    expect(updated.terminalSeen).toBe(false)
  })
  it("decodes split UTF-8 and CRLF frames without turning heartbeats into content", async () => {
    const frame = `: heartbeat\r\n\r\ndata: ${JSON.stringify(event(1, "assistant_delta", { message_id: "m", part_id: "0", text: "中文🙂" }))}\r\n\r\n`
    const bytes = new TextEncoder().encode(frame)
    const response = new Response(new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close() } }))
    const accepted: RunEvent[] = []
    await consumeRunEvents(response, item => accepted.push(item))
    expect(accepted).toHaveLength(1)
    expect(accepted[0].text).toBe("中文🙂")
  })
  it("leaves a truncated final frame unaccepted for cursor replay", async () => {
    const accepted: RunEvent[] = []
    await consumeRunEvents(new Response('data: {"type":"assistant_delta"'), item => accepted.push(item))
    expect(accepted).toEqual([])
  })
})
