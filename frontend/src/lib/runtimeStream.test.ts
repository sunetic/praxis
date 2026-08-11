import { describe, expect, it } from "vitest"

import { formatRuntimeCoreMessage, normalizeRuntimeStreamEvent } from "./runtimeStream"

describe("runtimeStream", () => {
  it("does not expose generic assistant names", () => {
    const event = normalizeRuntimeStreamEvent({
      type: "assistant",
      data: { text: "hello" },
    })

    expect(event).toEqual({
      kind: "assistant",
      text: "hello",
      source: "llm",
      agent: "",
    })
  })

  it("omits generic agent prefix in core progress text", () => {
    expect(
      formatRuntimeCoreMessage({
        kind: "core",
        name: "plan",
        status: "running",
        summary: "处理中",
        payload: {},
        source: "runtime",
        agent: "Assistant",
      })
    ).toBe("需求规划 · 处理中")
  })
})
import { describe, expect, it } from "vitest"

import {
  consumeRuntimeSse,
  formatRuntimeCoreMessage,
  normalizeRuntimeStreamEvent,
} from "./runtimeStream"

function createSseResponse(events: Array<Record<string, unknown>>): Response {
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  })
}

describe("runtimeStream", () => {
  it("normalizes phase event to core", () => {
    const event = normalizeRuntimeStreamEvent({
      type: "phase",
      phase: "intent_parsed",
      data: { status: "running", summary: "Plan · 已解析需求" },
    })
    expect(event?.kind).toBe("core")
    if (!event || event.kind !== "core") return
    expect(event.name).toBe("plan")
    expect(event.summary).toContain("Plan")
  })

  it("normalizes extension event", () => {
    const event = normalizeRuntimeStreamEvent({
      type: "extension",
      event_name: "verify_result",
      data: { summary: "Verify · 通过" },
    })
    expect(event?.kind).toBe("extension")
    if (!event || event.kind !== "extension") return
    expect(event.name).toBe("verify_result")
    expect(event.summary).toContain("Verify")
  })

  it("formats core message with duration", () => {
    const text = formatRuntimeCoreMessage({
      kind: "core",
      name: "act",
      status: "running",
      summary: "Act · 已应用补丁",
      payload: { phase_duration_ms: 88 },
      source: "runtime",
      agent: "FunctionBuilderAgent",
    })
    expect(text).toContain("88ms")
  })

  it("formats core summary with phase prefix when summary is plain text", () => {
    const text = formatRuntimeCoreMessage({
      kind: "core",
      name: "act",
      status: "done",
      summary: "已修复数据库名称提取逻辑。",
      payload: {},
      source: "runtime",
      agent: "FunctionBuilderAgent",
    })
    expect(text).toBe("FunctionBuilderAgent · 变更执行 · 已修复数据库名称提取逻辑。")
  })

  it("can omit agent prefix for progress cards", () => {
    const text = formatRuntimeCoreMessage(
      {
        kind: "core",
        name: "act",
        status: "done",
        summary: "已修复数据库名称提取逻辑。",
        payload: {},
        source: "runtime",
        agent: "FunctionBuilderAgent",
      },
      { includeAgent: false }
    )
    expect(text).toBe("变更执行 · 已修复数据库名称提取逻辑。")
  })

  it("normalizes attempt progress copy", () => {
    const text = formatRuntimeCoreMessage(
      {
        kind: "core",
        name: "observe",
        status: "running",
        summary: "Observe · Attempt 2 校验中",
        payload: {},
        source: "runtime",
        agent: "FunctionBuilderAgent",
      },
      { includeAgent: false }
    )
    expect(text).toBe("结果校验 · 第2轮校验中")
  })

  it("consumes sse and returns done payload", async () => {
    const response = createSseResponse([
      { type: "phase", phase: "plan", data: { status: "running", summary: "Plan · 进行中" } },
      { type: "assistant", data: { text: "最终说明" } },
      { type: "done", data: { status: "done", action: "build" } },
    ])
    const receivedKinds: string[] = []
    const result = await consumeRuntimeSse(response, {
      onEvent(event) {
        receivedKinds.push(event.kind)
      },
    })
    expect(receivedKinds).toContain("core")
    expect(receivedKinds).toContain("assistant")
    expect(String(result.donePayload.action || "")).toBe("build")
  })
})

import { consumeVds } from "./runtimeStream"

function createVdsResponse(lines: string[]): Response {
  const body = lines.join("\n") + "\n"
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  })
}

describe("consumeVds", () => {
  it("parses text delta (code 0)", async () => {
    const response = createVdsResponse([
      `0:"hello "`,
      `0:"world"`,
      `d:{"finishReason":"stop"}`,
    ])
    const texts: string[] = []
    const result = await consumeVds(response, {
      onEvent(event) {
        if (event.kind === "assistant") texts.push(event.text)
      },
    })
    expect(texts).toEqual(["hello ", "world"])
    expect(result.assistantText).toBe("hello world")
  })

  it("parses reasoning delta (code g) as core plan event", async () => {
    const response = createVdsResponse([
      `g:"thinking..."`,
      `d:{"finishReason":"stop"}`,
    ])
    const events: string[] = []
    await consumeVds(response, {
      onEvent(event) { events.push(`${event.kind}:${event.kind === "core" ? event.name : ""}`) },
    })
    expect(events).toContain("core:plan")
  })

  it("parses tool call (code 9) and result (code a)", async () => {
    const response = createVdsResponse([
      `9:{"toolCallId":"tc1","toolName":"query_sql","args":{"sql":"SELECT 1"}}`,
      `a:{"toolCallId":"tc1","result":"ok"}`,
      `d:{"finishReason":"stop"}`,
    ])
    const names: string[] = []
    await consumeVds(response, {
      onEvent(event) {
        if (event.kind === "extension") names.push(event.name)
      },
    })
    expect(names).toContain("tool_start")
    expect(names).toContain("tool_result")
  })

  it("parses data channel (code 2) items", async () => {
    const response = createVdsResponse([
      `2:[{"type":"reflect","action":"plan","message":"复盘中"}]`,
      `d:{"finishReason":"stop"}`,
    ])
    const types: string[] = []
    await consumeVds(response, {
      onEvent(event) { types.push(`${event.kind}:${event.kind === "core" ? event.name : (event.kind === "extension" ? event.name : "")}`) },
    })
    expect(types.some((t) => t.includes("reflect"))).toBe(true)
  })

  it("emits error event for d finishReason error", async () => {
    const response = createVdsResponse([
      `d:{"finishReason":"error"}`,
    ])
    const kinds: string[] = []
    const result = await consumeVds(response, {
      onEvent(event) { kinds.push(event.kind) },
    }).catch(() => ({ donePayload: {}, assistantText: "", terminalError: "请求失败" }))
    expect(result.terminalError ?? "").toBeTruthy()
  })

  it("preserves long-running progress and checkpoint events", async () => {
    const response = createVdsResponse([
      `2:[{"type":"progress","decision":"recoverable_failure","reason":"adjust strategy"}]`,
      `2:[{"type":"checkpoint","status":"stalled","reason":"same failure repeated"}]`,
      `2:[{"type":"done","status":"incomplete","completed":false,"task_run_id":"task-1"}]`,
      `d:{"finishReason":"stop"}`,
    ])
    const names: string[] = []
    const result = await consumeVds(response, {
      onEvent(event) {
        if (event.kind === "extension") names.push(event.name)
      },
    })

    expect(names).toEqual(["progress", "checkpoint"])
    expect(result.donePayload.status).toBe("incomplete")
    expect(result.donePayload.task_run_id).toBe("task-1")
  })
})

describe("long-running runtime normalization", () => {
  it("normalizes top-level VDS progress payload as an extension", () => {
    const event = normalizeRuntimeStreamEvent({
      type: "progress",
      decision: "candidate_complete",
      reason: "verifying",
      task_run_id: "task-1",
    })

    expect(event?.kind).toBe("extension")
    if (!event || event.kind !== "extension") return
    expect(event.name).toBe("progress")
    expect(event.payload.decision).toBe("candidate_complete")
    expect(event.payload.task_run_id).toBe("task-1")
  })
})
