import { act, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { renderWithShell as render } from "@/test/renderWithShell"
import { PageAgentChatShell } from "./PageAgentChatShell"

const { toastError, conversationsApi, chatApi, messagesApi, consumeRuntimeSse } = vi.hoisted(() => ({
  toastError: vi.fn(),
  conversationsApi: {
    create: vi.fn(),
  },
  chatApi: {
    stream: vi.fn(),
    listPendingActions: vi.fn(),
    listEvents: vi.fn(),
    confirmPendingAction: vi.fn(),
    cancelPendingAction: vi.fn(),
  },
  messagesApi: {
    create: vi.fn(),
    list: vi.fn(),
  },
  consumeRuntimeSse: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  conversationsApi,
  chatApi,
  messagesApi,
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: toastError },
}))

vi.mock("@/lib/runtimeStream", () => ({
  consumeRuntimeSse,
  consumeVds: consumeRuntimeSse,
  formatRuntimeCoreMessage: (event: { summary?: string }) => event.summary || "处理中",
}))

describe("PageAgentChatShell", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    toastError.mockReset()
    conversationsApi.create.mockResolvedValue({ id: 101 })
    chatApi.stream.mockResolvedValue({ ok: true, body: {} as ReadableStream<Uint8Array> })
    chatApi.listPendingActions.mockResolvedValue([])
    chatApi.listEvents.mockResolvedValue([])
    chatApi.confirmPendingAction.mockResolvedValue({ success: true, token: "token-1", status: "confirmed", result: {} })
    chatApi.cancelPendingAction.mockResolvedValue({ success: true, token: "token-1", status: "cancelled" })
    messagesApi.create.mockImplementation(async (params: { conversation_id: number; role: string; content: string }) => ({
      id: Date.now(),
      conversation_id: params.conversation_id,
      role: params.role,
      content: params.content,
      created_at: new Date().toISOString(),
    }))
    messagesApi.list.mockResolvedValue([])
    consumeRuntimeSse.mockImplementation(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "core", name: "plan", summary: "需求规划中" })
      options?.onEvent?.({ kind: "assistant", text: "这是诊断结论。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "这是诊断结论。" }
    })
  })

  it("sends prompt with page agent payload and renders assistant output", async () => {
    const user = userEvent.setup()
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 301,
        conversation_id: 101,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01Z",
        payload: { content: "帮我分析这个问题", message_id: 1 },
      },
      {
        id: 302,
        conversation_id: 101,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 1,
        role: "assistant",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
        payload: { content: "这是诊断结论。", event_kind: "assistant_text" },
      },
    ])
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 1,
        conversation_id: 101,
        role: "user",
        content: "帮我分析这个问题",
        created_at: "2026-03-14T00:00:01Z",
      },
      {
        id: 2,
        conversation_id: 101,
        role: "assistant",
        content: "这是诊断结论。",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
      },
    ])
    render(
      <PageAgentChatShell
        adapter={{
          page: "stats-analysis",
          profile: "stats_analysis_agent",
          sceneKey: "stats_analysis",
          tools: ["execute_sql"],
          skills: ["stats-analysis"],
          buildContext: () => ({ mode: "test" }),
        }}
        datasourceId={11}
        focusObject={{ type: "issue", issue_id: "failed:11" }}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "帮我分析这个问题{enter}")

    await waitFor(() => {
      expect(conversationsApi.create).toHaveBeenCalled()
      expect(chatApi.stream).toHaveBeenCalled()
    })
    expect(chatApi.stream).toHaveBeenCalledWith(
      101,
      "帮我分析这个问题",
      expect.objectContaining({
        runDatasourceIds: [11],
        sceneAgent: expect.objectContaining({
          key: "stats_analysis",
        }),
      })
    )
    expect(await screen.findByText("这是诊断结论。")).toBeInTheDocument()
  })

  it("applies suggested prompt and supports jump callback", async () => {
    const onJump = vi.fn()
    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
        focusObject={{ type: "risk_candidate", candidate_id: 1 }}
        suggestedPrompt="请继续分析"
        onSuggestedPromptApplied={() => {}}
        onJumpToFocusObject={onJump}
      />
    )

    expect(await screen.findByDisplayValue("请继续分析")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "回到对象详情" }))
    expect(onJump).toHaveBeenCalled()
  })

  it("creates a fresh conversation when freshSessionKey changes", async () => {
    const user = userEvent.setup()
    conversationsApi.create
      .mockResolvedValueOnce({ id: 101 })
      .mockResolvedValueOnce({ id: 102 })

    const { rerender } = render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
        focusObject={{ type: "issue", issue_id: "failed:11" }}
        freshSessionKey="ds:11:issue:failed:11"
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "第一次分析{enter}")
    await waitFor(() => {
      expect(conversationsApi.create).toHaveBeenCalledTimes(1)
      expect(messagesApi.create).toHaveBeenLastCalledWith(expect.objectContaining({ conversation_id: 101, content: "第一次分析" }))
    })

    rerender(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
        focusObject={{ type: "issue", issue_id: "dml:11" }}
        freshSessionKey="ds:11:issue:dml:11"
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "第二次分析{enter}")
    await waitFor(() => {
      expect(conversationsApi.create).toHaveBeenCalledTimes(2)
      expect(messagesApi.create).toHaveBeenLastCalledWith(expect.objectContaining({ conversation_id: 102, content: "第二次分析" }))
    })
  })

  it("recreates unmanaged scene conversation after stale conversation reset", async () => {
    const user = userEvent.setup()
    conversationsApi.create
      .mockResolvedValueOnce({ id: 101 })
      .mockResolvedValueOnce({ id: 102 })
    messagesApi.list.mockRejectedValueOnce({
      response: { status: 404, data: { detail: "Conversation not found" } },
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent", sceneKey: "stats_analysis" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "第一次分析{enter}")
    await waitFor(() => {
      expect(conversationsApi.create).toHaveBeenCalledTimes(1)
      expect(messagesApi.create).toHaveBeenCalledWith(expect.objectContaining({ conversation_id: 101, content: "第一次分析" }))
    })
    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith("原会话已失效，已重置当前对话。")
    })

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "第二次分析{enter}")
    await waitFor(() => {
      expect(conversationsApi.create).toHaveBeenCalledTimes(2)
      expect(messagesApi.create).toHaveBeenLastCalledWith(expect.objectContaining({ conversation_id: 102, content: "第二次分析" }))
    })
  })

  it("keeps tool cards after stream completion by reloading persisted events", async () => {
    const user = userEvent.setup()
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 301,
        conversation_id: 101,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01Z",
        payload: { content: "继续分析", message_id: 1 },
      },
      {
        id: 302,
        conversation_id: 101,
        event_type: "step_result",
        turn_seq: 1,
        part_seq: 1,
        created_at: "2026-03-14T00:00:02Z",
        payload: {
          kind: "tool",
          name: "execute_sql",
          arguments: '{"sql":"select 1"}',
          result: { success: true, data: { rows: [{ value: 1 }] } },
        },
      },
      {
        id: 303,
        conversation_id: 101,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 2,
        role: "assistant",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
        payload: { content: "这是诊断结论。", event_kind: "assistant_text" },
      },
    ])
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 1,
        conversation_id: 101,
        role: "user",
        content: "继续分析",
        created_at: "2026-03-14T00:00:01Z",
      },
      {
        id: 2,
        conversation_id: 101,
        role: "assistant",
        content: "这是诊断结论。",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
      },
    ])
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "extension", name: "tool_start", payload: { step_id: "s1", name: "execute_sql", arguments: '{"sql":"select 1"}' } })
      options?.onEvent?.({
        kind: "extension",
        name: "tool_result",
        payload: {
          step_id: "s1",
          name: "execute_sql",
          arguments: '{"sql":"select 1"}',
          result: { success: true, data: { rows: [{ value: 1 }] } },
        },
      })
      options?.onEvent?.({ kind: "assistant", text: "这是诊断结论。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "这是诊断结论。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "继续分析{enter}")

    expect(await screen.findByText("这是诊断结论。")).toBeInTheDocument()
    expect(await screen.findByText(/工具调用：execute_sql/)).toBeInTheDocument()
    await waitFor(() => expect(chatApi.listEvents).toHaveBeenCalledTimes(1))
  })

  it("keeps user and assistant messages visible when persisted events only contain tool results", async () => {
    const user = userEvent.setup()
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 302,
        conversation_id: 101,
        event_type: "step_result",
        turn_seq: 1,
        part_seq: 1,
        created_at: "2026-03-14T00:00:02Z",
        payload: {
          kind: "tool",
          name: "execute_sql",
          arguments: '{"sql":"select 1"}',
          result: { success: true, data: { rows: [{ value: 1 }] } },
        },
      },
    ])
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 1,
        conversation_id: 101,
        role: "user",
        content: "继续分析",
        created_at: "2026-03-14T00:00:01Z",
      },
      {
        id: 2,
        conversation_id: 101,
        role: "assistant",
        content: "这是诊断结论。",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
      },
    ])
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "extension", name: "tool_start", payload: { step_id: "s1", name: "execute_sql", arguments: '{"sql":"select 1"}' } })
      options?.onEvent?.({
        kind: "extension",
        name: "tool_result",
        payload: {
          step_id: "s1",
          name: "execute_sql",
          arguments: '{"sql":"select 1"}',
          result: { success: true, data: { rows: [{ value: 1 }] } },
        },
      })
      options?.onEvent?.({ kind: "assistant", text: "这是诊断结论。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "这是诊断结论。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "继续分析{enter}")

    expect(await screen.findByText("继续分析")).toBeInTheDocument()
    expect(await screen.findByText("这是诊断结论。")).toBeInTheDocument()
    expect(await screen.findByText(/工具调用：execute_sql/)).toBeInTheDocument()
  })

  it("preserves streamed assistant output after abort", async () => {
    const user = userEvent.setup()
    let abortSignal: AbortSignal | undefined
    let releaseStream: (() => void) | null = null
    const streamBlocked = new Promise<void>((resolve) => {
      releaseStream = resolve
    })
    chatApi.stream.mockImplementationOnce(async (_conversationId: number, _content: string, options?: { signal?: AbortSignal }) => {
      abortSignal = options?.signal
      return { ok: true, body: {} as ReadableStream<Uint8Array> }
    })
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "assistant", text: "这是中止前已显示的内容。" })
      await streamBlocked
      if (abortSignal?.aborted) throw new DOMException("The operation was aborted.", "AbortError")
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "这是中止前已显示的内容。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "继续分析{enter}")
    expect(await screen.findByText("这是中止前已显示的内容。", { exact: false })).toBeInTheDocument()

    await act(async () => {
      await user.click(await screen.findByRole("button", { name: "停止生成" }))
      releaseStream?.()
    })

    await waitFor(() => {
      expect(screen.getAllByText("这是中止前已显示的内容。", { exact: false }).length).toBeGreaterThan(0)
    })
  })

  it("supports confirm action from tool card", async () => {
    const user = userEvent.setup()
    chatApi.listPendingActions.mockResolvedValue([
      {
        token: "token-1",
        action_type: "sql",
        status: "pending",
        sql_preview: "UPDATE t SET a=1",
      },
    ])
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "extension", name: "tool_start", payload: { step_id: "s1", name: "execute_sql" } })
      options?.onEvent?.({
        kind: "extension",
        name: "tool_result",
        payload: {
          step_id: "s1",
          name: "execute_sql",
          result: { success: true, data: { requires_confirmation: true, action_token: "token-1" } },
        },
      })
      options?.onEvent?.({ kind: "assistant", text: "请确认执行。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "请确认执行。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "执行变更{enter}")
    await user.click(await screen.findByRole("button", { name: "确认执行" }))

    await waitFor(() => {
      expect(chatApi.confirmPendingAction).toHaveBeenCalledWith(101, "token-1")
    })
  })

  it("hides stale confirm button after confirm failure refresh", async () => {
    const user = userEvent.setup()
    chatApi.listPendingActions
      .mockResolvedValueOnce([
        {
          token: "token-1",
          action_type: "sql",
          status: "pending",
          sql_preview: "UPDATE t SET a=1",
        },
      ])
      .mockResolvedValueOnce([])
    chatApi.confirmPendingAction.mockRejectedValueOnce({
      response: { data: { detail: "Pending action not found" } },
    })
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 401,
        conversation_id: 101,
        event_type: "step_result",
        turn_seq: 1,
        part_seq: 1,
        created_at: "2026-03-14T00:00:02Z",
        payload: {
          kind: "tool",
          name: "execute_sql",
          arguments: '{"sql":"update t set a=1"}',
          result: {
            success: false,
            data: {
              requires_confirmation: false,
              action_token: "token-1",
              confirmed_action_token: "token-1",
            },
            error: { code: "sql_execution_error", message: "Pending action not found" },
          },
        },
      },
    ])
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 11,
        conversation_id: 101,
        role: "assistant",
        content: "这次确认后的 SQL 执行失败了，核心报错是 Pending action not found。建议先检查待确认卡片是否已过期，再重新发起一次操作。",
        created_at: "2026-03-14T00:00:03Z",
      },
    ])
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "extension", name: "tool_start", payload: { step_id: "s1", name: "execute_sql" } })
      options?.onEvent?.({
        kind: "extension",
        name: "tool_result",
        payload: {
          step_id: "s1",
          name: "execute_sql",
          result: { success: true, data: { requires_confirmation: true, action_token: "token-1" } },
        },
      })
      options?.onEvent?.({ kind: "assistant", text: "请确认执行。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "请确认执行。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "执行变更{enter}")
    await user.click(await screen.findByRole("button", { name: "确认执行" }))

    await waitFor(() => {
      expect(chatApi.listEvents).toHaveBeenCalledWith(101)
    })
    expect(screen.queryByRole("button", { name: "确认执行" })).not.toBeInTheDocument()
    expect(await screen.findByText(/执行失败：Pending action not found/)).toBeInTheDocument()
    expect(
      await screen.findByText(
        "这次确认后的 SQL 执行失败了，核心报错是 Pending action not found。建议先检查待确认卡片是否已过期，再重新发起一次操作。"
      )
    ).toBeInTheDocument()
  })

  it("does not bootstrap active_skills when scene agent already carries skills", async () => {
    const user = userEvent.setup()
    conversationsApi.create.mockResolvedValueOnce({ id: 202 })

    render(
      <PageAgentChatShell
        adapter={{
          page: "stats-analysis",
          profile: "stats_analysis_agent",
          sceneKey: "stats_analysis",
          skills: ["stats-analysis"],
        }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "继续分析{enter}")

    await waitFor(() => {
      expect(conversationsApi.create).toHaveBeenCalledTimes(1)
      expect(chatApi.stream).toHaveBeenCalledWith(
        202,
        "继续分析",
        expect.objectContaining({
          runDatasourceIds: [11],
          sceneAgent: expect.objectContaining({ key: "stats_analysis" }),
        })
      )
    })
    expect(conversationsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "stats-analysis · Agent Chat",
        datasource_id: 11,
        category: "scene",
        scene_key: "stats_analysis",
        read_only: true,
      })
    )
  })

  it("deduplicates auto-send suggested prompt in StrictMode", async () => {
    render(
      <StrictMode>
        <PageAgentChatShell
          adapter={{ page: "stats-analysis", profile: "stats_analysis_agent", sceneKey: "stats_analysis" }}
          datasourceId={11}
          suggestedPrompt="请继续分析"
          autoSendSuggestedPrompt={true}
          onSuggestedPromptApplied={() => {}}
        />
      </StrictMode>
    )

    await waitFor(() => {
      expect(chatApi.stream).toHaveBeenCalledTimes(1)
    })
  })

  it("does not render generic phase cards when runtime events have no summary", async () => {
    const user = userEvent.setup()
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 311,
        conversation_id: 101,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01Z",
        payload: { content: "继续分析", message_id: 1 },
      },
      {
        id: 312,
        conversation_id: 101,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 1,
        role: "assistant",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
        payload: { content: "这是最终结论。", event_kind: "assistant_text" },
      },
    ])
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 1,
        conversation_id: 101,
        role: "user",
        content: "继续分析",
        created_at: "2026-03-14T00:00:01Z",
      },
      {
        id: 2,
        conversation_id: 101,
        role: "assistant",
        content: "这是最终结论。",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
      },
    ])
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "core", name: "plan", summary: "", payload: {}, agent: "" })
      options?.onEvent?.({ kind: "core", name: "reflect", summary: "", payload: {}, agent: "" })
      options?.onEvent?.({ kind: "assistant", text: "这是最终结论。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "这是最终结论。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "继续分析{enter}")

    expect(await screen.findByText("这是最终结论。")).toBeInTheDocument()
    expect(screen.queryByText("需求规划 · 处理中")).not.toBeInTheDocument()
    expect(screen.queryByText("策略复盘 · 处理中")).not.toBeInTheDocument()
  })

  it("does not duplicate runtime progress text in both card and status indicator", async () => {
    const user = userEvent.setup()
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 321,
        conversation_id: 101,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01Z",
        payload: { content: "继续分析", message_id: 1 },
      },
      {
        id: 322,
        conversation_id: 101,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 1,
        role: "assistant",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
        payload: { content: "处理完成。", event_kind: "assistant_text" },
      },
    ])
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 1,
        conversation_id: 101,
        role: "user",
        content: "继续分析",
        created_at: "2026-03-14T00:00:01Z",
      },
      {
        id: 2,
        conversation_id: 101,
        role: "assistant",
        content: "处理完成。",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
      },
    ])
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "core", name: "act", summary: "变更执行 · 根据数据源 ID 检查当前租户的统计信息健康状况", payload: {}, agent: "FunctionBuilderAgent" })
      options?.onEvent?.({ kind: "assistant", text: "处理完成。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "处理完成。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "继续分析{enter}")

    expect(await screen.findByText("处理完成。")).toBeInTheDocument()
    expect(screen.queryByText("变更执行 · 根据数据源 ID 检查当前租户的统计信息健康状况")).not.toBeInTheDocument()
  })

  it("uses read-only completion copy instead of confirmation copy for read-only tool results", async () => {
    const user = userEvent.setup()
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 331,
        conversation_id: 101,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01Z",
        payload: { content: "这张表是否需要直方图？依据是什么？", message_id: 1 },
      },
      {
        id: 332,
        conversation_id: 101,
        event_type: "step_result",
        turn_seq: 1,
        part_seq: 1,
        created_at: "2026-03-14T00:00:02Z",
        payload: {
          kind: "tool",
          name: "execute_sql",
          arguments: '{"sql":"select 1","intent":"检查表结构"}',
          result: { success: true, data: { rows: [{ value: 1 }], row_count: 1, resolved_role: "user" } },
        },
      },
      {
        id: 333,
        conversation_id: 101,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 2,
        role: "assistant",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
        payload: { content: "已完成只读检查。", event_kind: "assistant_text" },
      },
    ])
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 1,
        conversation_id: 101,
        role: "user",
        content: "这张表是否需要直方图？依据是什么？",
        created_at: "2026-03-14T00:00:01Z",
      },
      {
        id: 2,
        conversation_id: 101,
        role: "assistant",
        content: "已完成只读检查。",
        agent_name: "StatsAnalysisAgent",
        created_at: "2026-03-14T00:00:03Z",
      },
    ])
    consumeRuntimeSse.mockImplementationOnce(async (_resp: unknown, options?: { onEvent?: (event: unknown) => void }) => {
      options?.onEvent?.({ kind: "extension", name: "tool_start", payload: { step_id: "s1", name: "execute_sql", arguments: '{"sql":"select 1","intent":"检查表结构"}' } })
      options?.onEvent?.({
        kind: "extension",
        name: "tool_result",
        payload: {
          step_id: "s1",
          name: "execute_sql",
          arguments: '{"sql":"select 1","intent":"检查表结构"}',
          result: { success: true, data: { rows: [{ value: 1 }], row_count: 1, resolved_role: "user" } },
        },
      })
      options?.onEvent?.({ kind: "assistant", text: "已完成只读检查。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "已完成只读检查。" }
    })

    render(
      <PageAgentChatShell
        adapter={{ page: "stats-analysis", profile: "stats_analysis_agent" }}
        datasourceId={11}
      />
    )

    await user.type(screen.getByPlaceholderText("输入诊断问题..."), "这张表是否需要直方图？依据是什么？{enter}")

    expect(await screen.findByText("已完成只读检查。")).toBeInTheDocument()
    expect(await screen.findByText("执行成功，返回 1 条记录")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "确认执行" })).not.toBeInTheDocument()
    expect(screen.queryByText(/已生成变更确认卡/)).not.toBeInTheDocument()
  })
})
