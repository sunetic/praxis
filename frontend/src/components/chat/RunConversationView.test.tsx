import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderWithShell as render } from "@/test/renderWithShell"
import { agentRunsApi, type RunEvent } from "@/lib/agentRuns"
import { RunConversationView } from "./RunConversationView"

const run = { id: "run-1", conversation_id: "conv", prompt: "原始请求", seq: 1, status: "queued" as const, cancel_requested: false, output: null, error_code: null, event_seq: 0, created_at: 1 }
const event = (seq: number, type: string, patch: Partial<RunEvent> = {}): RunEvent => ({ run_id: run.id, seq, type, ...patch })
function channel() {
  let controller: ReadableStreamDefaultController<Uint8Array>
  const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value } }))
  return { response, push: (item: RunEvent) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(item)}\n\n`)), close: () => controller.close() }
}

beforeEach(() => { vi.spyOn(agentRunsApi, "runs").mockResolvedValue([]) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("native chat interaction", () => {
  it("preserves reconciliation evidence on failure and does not resume implicitly", async () => {
    vi.mocked(agentRunsApi.runs).mockResolvedValue([{ ...run, status: "interrupted", event_seq: 2 }])
    const stream = channel()
    vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    const reconcile = vi.spyOn(agentRunsApi, "reconcile").mockRejectedValue(new Error("offline"))
    const resume = vi.spyOn(agentRunsApi, "resume")
    render(<RunConversationView conversationId="conv" />)
    await act(async () => {
      stream.push(event(1, "tool_start", { call_id: "unknown", name: "request_database_change", fingerprint: "original" }))
      stream.push(event(2, "run_interrupted", { status: "interrupted" })); stream.close()
    })
    const record = await screen.findByRole("button", { name: "记录核对结果", exact: true })
    expect(record).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText("核对结果", { exact: true }), "succeeded")
    const evidence = screen.getByRole("textbox", { name: /核对依据/ })
    await userEvent.type(evidence, "独立查询确认 n=1，原连接已关闭")
    expect(record).toBeDisabled()
    await userEvent.click(screen.getByRole("checkbox", { name: /我已确认外部操作结束/ }))
    await userEvent.click(record)
    await screen.findByText(/核对结果未能确认/)
    expect(evidence).toHaveValue("独立查询确认 n=1，原连接已关闭")
    expect(screen.getByRole("button", { name: "继续此运行" })).toBeDisabled()
    expect(reconcile).toHaveBeenCalledWith(run.id, "unknown", "original", { resolution: "succeeded", evidence: "独立查询确认 n=1，原连接已关闭", execution_stopped: true })
    expect(resume).not.toHaveBeenCalled()
  })

  it("shows a user message before submission finishes, then appends text without duplicating a final answer", async () => {
    let accept!: (value: typeof run) => void
    const submit = vi.spyOn(agentRunsApi, "submit").mockImplementation(() => new Promise(resolve => { accept = resolve }))
    const stream = channel()
    vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    render(<RunConversationView conversationId="conv" />)
    const input = screen.getByRole("textbox", { name: "消息" })
    await waitFor(() => expect(input).toBeEnabled())
    await userEvent.type(input, "你好")
    await userEvent.click(screen.getByRole("button", { name: "发送" }))
    expect(screen.getByText("你好")).toBeInTheDocument()
    expect(screen.getByText("正在提交…")).toBeInTheDocument()
    expect(submit).toHaveBeenCalledTimes(1)
    await act(async () => { accept({ ...run, prompt: "你好" }) })
    await act(async () => {
      stream.push(event(1, "request_started"))
      stream.push(event(2, "assistant_delta", { message_id: "m1", part_id: "0", text: "你好，" }))
      stream.push(event(3, "assistant_delta", { message_id: "m1", part_id: "0", text: "有什么问题？" }))
      stream.push(event(4, "assistant_message_end", { message_id: "m1" }))
      stream.push(event(5, "run_finished", { status: "finished" })); stream.close()
    })
    await waitFor(() => expect(screen.getAllByText("你好，有什么问题？")).toHaveLength(1))
    expect(screen.queryByText("任务成功")).not.toBeInTheDocument()
  })

  it("sends the selected English interface locale as run context", async () => {
    const submit = vi.spyOn(agentRunsApi, "submit").mockImplementation(() => new Promise(() => {}))
    render(<RunConversationView conversationId="conv" scene={{ datasource_ids: [3] }} />, { locale: "en-US" })
    const input = screen.getByRole("textbox", { name: "Message" })
    await waitFor(() => expect(input).toBeEnabled())
    await userEvent.type(input, "SELECT 1")
    await userEvent.click(screen.getByRole("button", { name: "Send" }))
    expect(submit).toHaveBeenCalledWith(
      "conv",
      expect.any(String),
      "SELECT 1",
      { datasource_ids: [3], locale: "en-US" },
      "append",
    )
  })

  it("shows context-window usage and the compaction transition beside the composer", async () => {
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    const stream = channel()
    vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    render(<RunConversationView conversationId="conv" />)
    await act(async () => {
      stream.push(event(1, "context_status", { context_window_tokens: 1000, estimated_tokens: 500, used_percent: 50, compression_threshold_percent: 75, compression_threshold_tokens: 750, remaining_tokens: 500, token_source: "estimate", state: "ready" }))
    })
    await waitFor(() => expect(screen.getByTestId("chat-context-usage")).toHaveTextContent("50.0%"))
    expect(screen.getByRole("progressbar", { name: "上下文 50.0%" })).toBeInTheDocument()
    await act(async () => { stream.push(event(2, "context_compaction_started", { context_window_tokens: 1000, before_context_tokens: 760, compression_threshold_percent: 75 })) })
    expect(await screen.findByText("正在压缩上下文")).toBeInTheDocument()
    await act(async () => { stream.push(event(3, "context_compacted", { context_window_tokens: 1000, before_context_tokens: 760, after_context_tokens: 320, source_indices: [0, 1] })) })
    expect(await screen.findByTestId("chat-context-compressed")).toHaveTextContent("76.0% → 32.0%")
    expect(screen.getByTestId("chat-context-usage")).toHaveTextContent("32.0%")
  })

  it("retries an unconfirmed submission with the original client request ID", async () => {
    const submit = vi.spyOn(agentRunsApi, "submit").mockRejectedValueOnce(new Error("offline")).mockResolvedValue(run)
    const stream = channel()
    vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    render(<RunConversationView conversationId="conv" />)
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled())
    await userEvent.type(screen.getByRole("textbox"), "读取信息")
    await userEvent.click(screen.getByRole("button", { name: "发送" }))
    await userEvent.click(await screen.findByRole("button", { name: "重试提交" }))
    expect(submit).toHaveBeenCalledTimes(2)
    expect(submit.mock.calls[0]).toEqual(submit.mock.calls[1])
  })

  it("records approval without claiming success and keeps the same card for its result", async () => {
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    const stream = channel()
    vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    const decide = vi.spyOn(agentRunsApi, "decide").mockResolvedValue({ decision: "approved" })
    render(<RunConversationView conversationId="conv" />)
    await act(async () => { stream.push(event(1, "approval_required", { call_id: "write-1", name: "save_file", fingerprint: "exact-version", arguments: { path: "draft.py" }, target: { revision: 1 } })) })
    await userEvent.click(await screen.findByRole("button", { name: "批准此操作" }))
    expect(decide).toHaveBeenCalledWith(run.id, "write-1", "exact-version", true)
    expect(await screen.findByText(/决定已提交/)).toBeInTheDocument()
    expect(screen.queryByText("已执行")).not.toBeInTheDocument()
    await act(async () => { stream.push(event(2, "tool_result", { call_id: "write-1", status: "succeeded", content: "saved" })) })
    await waitFor(() => expect(screen.getByText("已执行")).toBeInTheDocument())
    expect(document.querySelectorAll('[data-tool-id="write-1"]')).toHaveLength(1)
  })

  it("does not scroll a reader back down, and distinguishes queue from stop-and-modify", async () => {
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    const stream = channel()
    vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    const submit = vi.spyOn(agentRunsApi, "submit").mockImplementation(() => new Promise(() => {}))
    render(<RunConversationView conversationId="conv" />)
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled())
    const viewport = screen.getByTestId("run-viewport")
    Object.defineProperties(viewport, { scrollHeight: { value: 2000, configurable: true }, clientHeight: { value: 400, configurable: true } })
    viewport.scrollTop = 100; fireEvent.scroll(viewport)
    await act(async () => { stream.push(event(1, "assistant_delta", { message_id: "m", part_id: "0", text: "继续输出" })) })
    await screen.findByText("继续输出")
    expect(screen.getByRole("button", { name: "停止", exact: true }).querySelector("svg")).toBeNull()
    expect(viewport.scrollTop).toBe(100)
    expect(screen.getByRole("button", { name: "回到底部" })).toBeInTheDocument()
    await userEvent.type(screen.getByRole("textbox"), "改成新的要求")
    await userEvent.click(screen.getByRole("button", { name: "停止并修改" }))
    expect(submit.mock.calls[0][4]).toBe("stop_and_modify")
    expect(screen.getByRole("button", { name: "排队发送" })).toBeInTheDocument()
  })

  it("disconnecting a view aborts only its subscription, not the server run", async () => {
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    const stream = channel()
    const subscription = vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    const cancel = vi.spyOn(agentRunsApi, "cancel")
    const view = render(<RunConversationView conversationId="conv" />)
    await waitFor(() => expect(subscription).toHaveBeenCalled())
    view.unmount()
    expect(subscription.mock.calls[0][2].aborted).toBe(true)
    expect(cancel).not.toHaveBeenCalled()
  })

  it("follows layout resize only while at the latest answer and releases its observer", async () => {
    let resized!: ResizeObserverCallback
    const disconnect = vi.fn()
    const original = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) { resized = callback }
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = disconnect
    }
    try {
      const view = render(<RunConversationView conversationId="conv" />)
      await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled())
      const viewport = screen.getByTestId("run-viewport")
      Object.defineProperties(viewport, { scrollHeight: { value: 2000, configurable: true }, clientHeight: { value: 400, configurable: true } })
      act(() => resized([], {} as ResizeObserver))
      expect(viewport.scrollTop).toBe(2000)
      viewport.scrollTop = 100; fireEvent.scroll(viewport)
      act(() => resized([], {} as ResizeObserver))
      expect(viewport.scrollTop).toBe(100)
      view.unmount()
      expect(disconnect).toHaveBeenCalledOnce()
    } finally { globalThis.ResizeObserver = original }
  })

  it("does not roll a terminal stream state back when an older cancel response arrives late", async () => {
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    const stream = channel()
    vi.spyOn(agentRunsApi, "stream").mockResolvedValue(stream.response)
    let finishCancel!: (value: typeof run & { cancel_requested: boolean }) => void
    vi.spyOn(agentRunsApi, "cancel").mockImplementation(() => new Promise(resolve => { finishCancel = resolve }))
    render(<RunConversationView conversationId="conv" />)
    await userEvent.click(await screen.findByRole("button", { name: "停止", exact: true }))
    await act(async () => { stream.push(event(1, "run_cancelled", { status: "cancelled" })); stream.close() })
    await screen.findByText("已停止")
    await act(async () => { finishCancel({ ...run, cancel_requested: true }) })
    expect(screen.getByText("已停止")).toBeInTheDocument()
    expect(screen.queryByText(/正在停止/)).not.toBeInTheDocument()
  })

  it("makes an inaccessible stream actionable without endlessly retrying or resubmitting", async () => {
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    const stream = channel()
    const subscription = vi.spyOn(agentRunsApi, "stream").mockResolvedValueOnce(new Response(null, { status: 403 })).mockResolvedValue(stream.response)
    const submit = vi.spyOn(agentRunsApi, "submit")
    render(<RunConversationView conversationId="conv" />)
    await screen.findByText(/无法读取运行记录/)
    expect(subscription).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole("button", { name: "重新连接" }))
    expect(subscription).toHaveBeenCalledTimes(2)
    expect(subscription.mock.calls[1][1]).toBe(0)
    expect(submit).not.toHaveBeenCalled()
  })
})
