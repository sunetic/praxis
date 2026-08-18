import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"

import { renderWithShell as render } from "@/test/renderWithShell"
import { ChatPage } from "./ChatPage"

const {
  conversationsApi,
  datasourcesApi,
  messagesApi,
  chatApi,
  skillsApi,
} = vi.hoisted(() => ({
  conversationsApi: {
    list: vi.fn(),
    create: vi.fn(),
    delete: vi.fn(),
  },
  datasourcesApi: {
    list: vi.fn(),
  },
  messagesApi: {
    list: vi.fn(),
    create: vi.fn(),
  },
  chatApi: {
    stream: vi.fn(),
    getContextStatus: vi.fn(),
    saveAgentStream: vi.fn(),
    listEvents: vi.fn(),
    createHandoff: vi.fn(),
    getHandoff: vi.fn(),
    consumeHandoff: vi.fn(),
    listPendingActions: vi.fn(),
    confirmPendingAction: vi.fn(),
    cancelPendingAction: vi.fn(),
  },
  skillsApi: {
    list: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  conversationsApi,
  datasourcesApi,
  messagesApi,
  chatApi,
  skillsApi,
}))

// Converts old-style SSE event objects to Vercel Data Stream lines
function sseObjToVds(obj: { type: string; data?: Record<string, unknown>; [key: string]: unknown }): string {
  const { type, data = {} } = obj
  if (type === "assistant") {
    const text = String(data.text ?? "")
    return `0:${JSON.stringify(text)}\n`
  }
  // Preserve the original {type, data} structure in the 2: channel
  return `2:${JSON.stringify([{ type, data }])}\n`
}

function sseResponse(chunks: string[]): Response {
  // chunks may be old-style `data: {...}\n\n` strings — convert to VDS
  const lines: string[] = []
  for (const chunk of chunks) {
    const match = chunk.match(/^data:\s*(\{.*\})\n\n?$/)
    if (match) {
      try {
        const obj = JSON.parse(match[1]) as { type: string; data: Record<string, unknown> }
        lines.push(sseObjToVds(obj))
        // Append done terminator after done or error events
        if (obj.type === "done" || obj.type === "error") {
          lines.push(`d:{"finishReason":"stop"}\n`)
        }
        continue
      } catch {
        // fall through
      }
    }
    lines.push(chunk)
  }
  return new Response(lines.join(""), {
    status: 200,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  })
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>
}

function buildConversation(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    title: "对话1",
    datasource_id: 1,
    agent_id: 1,
    active_skills: [],
    category: "primary",
    scene_key: null,
    read_only: false,
    created_at: "2026-03-14T00:00:00Z",
    updated_at: "2026-03-14T00:00:00Z",
    ...overrides,
  }
}

describe("ChatPage workspace boundary and handoff", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    conversationsApi.list.mockImplementation(async (params?: { category?: string }) => {
      if (params?.category === "scene") return []
      if (params?.category === "agent_run") return []
      return [buildConversation()]
    })
    conversationsApi.create.mockResolvedValue({
      id: 2,
      title: "新对话",
      datasource_id: 1,
      agent_id: 1,
      active_skills: [],
      category: "primary",
      scene_key: null,
      read_only: false,
      created_at: "2026-03-14T00:00:00Z",
      updated_at: "2026-03-14T00:00:00Z",
    })
    conversationsApi.delete.mockResolvedValue({})
    datasourcesApi.list.mockResolvedValue([
      {
        id: 1,
        name: "OB-1",
        host: "127.0.0.1",
        port: 2881,
        db_type: "oceanbase",
        cluster_key: "cluster-a",
        tenant_role: "user",
        user: "tenant",
        database: "test",
        status: "active",
        created_at: "2026-03-14T00:00:00Z",
        updated_at: "2026-03-14T00:00:00Z",
      },
    ])
    skillsApi.list.mockResolvedValue([])
    messagesApi.list.mockResolvedValue([])
    messagesApi.create.mockResolvedValue({
      id: 10,
      conversation_id: 1,
      role: "user",
      content: "hello",
      created_at: "2026-03-14T00:00:01Z",
    })
    chatApi.listEvents.mockResolvedValue([])
    chatApi.getHandoff.mockResolvedValue({
      id: 77,
      conversation_id: 1,
      status: "pending",
      created_at: "2026-03-14T00:00:00Z",
      packet: {
        type: "sql_analysis_live",
        version: 1,
        source: { page: "sql_analysis", entry: "drawer", label: "SQL Analysis" },
        title: "继续分析 SQL sql-2",
        summary: "app_db · 1 个诊断信号",
        facts: [
          { label: "DB", value: "app_db" },
          { label: "SQL ID", value: "sql-2" },
        ],
        suggested_prompts: ["继续分析这条 SQL 的主要风险"],
        context: {},
      },
    })
    chatApi.listPendingActions.mockResolvedValue([])
    chatApi.getContextStatus.mockResolvedValue(null)
    chatApi.confirmPendingAction.mockResolvedValue({})
    chatApi.cancelPendingAction.mockResolvedValue({})
    chatApi.saveAgentStream.mockResolvedValue(
      sseResponse([
        'data: {"type":"save_agent_status","data":{"stage":"summarizing_context","message":"总结上下文中..."}}\n\n',
        'data: {"type":"save_agent_status","data":{"stage":"saving_agent","message":"保存 Agent 中..."}}\n\n',
        'data: {"type":"save_agent_done","data":{"agent_id":101,"agent_name":"慢 SQL Agent","agent_url":"/agent?editAgentId=101","message":"已保存，可跳转到 Agent 编辑页继续修改。"}}\n\n',
        'data: {"type":"done","data":{"trace_id":"trace-1"}}\n\n',
      ])
    )
    chatApi.stream.mockResolvedValue(
      sseResponse([
        'data: {"type":"assistant","data":{"text":"ok"}}\n\n',
        'data: {"type":"done","data":{"text_emitted":true}}\n\n',
      ])
    )
  })

  it("keeps chat workspace free of object build controls", async () => {
    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    await waitFor(() => expect(screen.getByPlaceholderText("输入问题...")).toBeInTheDocument())
    expect(screen.queryByText("进入 Build Mode")).not.toBeInTheDocument()
    expect(screen.queryByText("Build Scope")).not.toBeInTheDocument()
    expect(screen.queryByTestId("build-preview-pane")).not.toBeInTheDocument()
  })

  it("keeps the main chat column shrinkable for long content", async () => {
    const { container } = render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    await waitFor(() => expect(screen.getByPlaceholderText("输入问题...")).toBeInTheDocument())

    const workbenchRoot = container.firstElementChild as HTMLElement | null
    const primaryGrid = container.querySelector('[data-slot="workbench-primary"] > div') as HTMLElement | null
    const mainColumn = primaryGrid?.lastElementChild as HTMLElement | null
    const composerInput = screen.getByPlaceholderText("输入问题...")
    const panelRoot = composerInput.closest("div.rounded-xl.border.border-border.bg-card") as HTMLElement | null

    expect(workbenchRoot).not.toBeNull()
    expect(primaryGrid).not.toBeNull()
    expect(mainColumn).not.toBeNull()
    expect(mainColumn).toHaveClass("min-w-0")
    expect(panelRoot).not.toBeNull()
    expect(panelRoot).toHaveClass("min-w-0")
    expect(panelRoot).toHaveClass("max-w-full")
  })

  it("keeps persisted assistant-tool-assistant order after refresh", async () => {
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 11,
        conversation_id: 1,
        role: "user",
        content: "那给出最近一小时的集群 CPU 负载情况",
        created_at: "2026-03-14T00:00:01.000000",
      },
    ])
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 101,
        conversation_id: 1,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01.000000",
        payload: { content: "那给出最近一小时的集群 CPU 负载情况", message_id: 11 },
      },
      {
        id: 102,
        conversation_id: 1,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 1,
        role: "assistant",
        agent_name: "ChatAgent",
        created_at: "2026-03-14T00:00:02.000000",
        payload: { content: "由于当前数据源缺少 OCP 集群关联信息，无法直接调用 OCP API 获取监控数据。", event_kind: "assistant_text" },
      },
      {
        id: 103,
        conversation_id: 1,
        event_type: "step_result",
        turn_seq: 1,
        part_seq: 2,
        created_at: "2026-03-14T00:00:03.000000",
        payload: {
          kind: "tool",
          name: "execute_sql",
          arguments: JSON.stringify({
            intent: "查询最近一小时各服务器的 CPU 负载统计",
            sql: "SELECT 1",
          }),
          result: {
            success: false,
            error: {
              code: "sql_execution_error",
              message: "SQL execution error",
            },
          },
        },
      },
      {
        id: 104,
        conversation_id: 1,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 3,
        role: "assistant",
        agent_name: "ChatAgent",
        created_at: "2026-03-14T00:00:04.000000",
        payload: { content: "不过我可以尝试通过数据库查询来获取 CPU 负载信息。", event_kind: "assistant_text" },
      },
    ])

    const { container } = render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    const leading = await screen.findByText(
      "由于当前数据源缺少 OCP 集群关联信息，无法直接调用 OCP API 获取监控数据。"
    )
    expect(leading).toBeInTheDocument()
    expect(await screen.findByText(/工具调用：execute_sql/)).toBeInTheDocument()
    expect(
      await screen.findByText("不过我可以尝试通过数据库查询来获取 CPU 负载信息。")
    ).toBeInTheDocument()

    const text = container.textContent || ""
    expect(
      text.indexOf("由于当前数据源缺少 OCP 集群关联信息，无法直接调用 OCP API 获取监控数据。")
    ).toBeLessThan(text.indexOf("工具调用：execute_sql"))
    expect(text.indexOf("工具调用：execute_sql")).toBeLessThan(
      text.indexOf("不过我可以尝试通过数据库查询来获取 CPU 负载信息。")
    )
  })

  it("does not duplicate persisted progress and tool parts with matching history events", async () => {
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 11,
        conversation_id: 1,
        role: "user",
        content: "检索 MySQL 8.0 文档",
        created_at: "2026-03-14T00:00:01.000000",
      },
      {
        id: 12,
        conversation_id: 1,
        role: "assistant",
        content: "检索完成。",
        content_parts: [
          { type: "progress", text: "正在检索知识库。", stage: "searching" },
          {
            type: "tool_use",
            id: "kb-step-1",
            name: "knowledge_search",
            input: { db_type: "mysql", version: "8.0" },
            result: { success: true },
          },
          { type: "text", text: "检索完成。" },
        ],
        created_at: "2026-03-14T00:00:04.000000",
      },
    ])
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 301,
        conversation_id: 1,
        event_type: "assistant_progress",
        turn_seq: 1,
        part_seq: 1,
        role: "assistant",
        created_at: "2026-03-14T00:00:02.000000",
        payload: { text: "正在检索知识库。" },
      },
      {
        id: 302,
        conversation_id: 1,
        event_type: "step_result",
        turn_seq: 1,
        part_seq: 2,
        created_at: "2026-03-14T00:00:03.000000",
        payload: {
          step_id: "kb-step-1",
          name: "knowledge_search",
          input: { db_type: "mysql", version: "8.0" },
          result: { success: true },
        },
      },
      {
        id: 303,
        conversation_id: 1,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 3,
        role: "assistant",
        created_at: "2026-03-14T00:00:04.000000",
        payload: { content: "检索完成。" },
      },
    ])

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getAllByText("正在检索知识库。")).toHaveLength(1)
      expect(screen.getAllByRole("button", { name: /工具调用：knowledge_search/i })).toHaveLength(1)
      expect(screen.getAllByText("检索完成。")).toHaveLength(1)
    })
  })

  it("shows handoff summary and sends handoff id on first turn", async () => {
    render(
      <MemoryRouter initialEntries={["/chat?conversationId=1&handoffId=77"]}>
        <ChatPage />
      </MemoryRouter>
    )

    expect(await screen.findByText("继续分析 SQL sql-2")).toBeInTheDocument()
    expect(screen.getByText("来自：SQL Analysis")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "继续分析这条 SQL 的主要风险" }))

    await waitFor(() =>
      expect(chatApi.stream).toHaveBeenCalledWith(
        1,
        "继续分析这条 SQL 的主要风险",
        expect.objectContaining({ handoffId: 77 })
      )
    )
  })

  it("keeps reusable chat suggestions out of the page-builder flow", async () => {
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 11,
        conversation_id: 1,
        role: "assistant",
        content: "这个需求可复用。",
        created_at: "2026-03-14T00:00:02Z",
      },
    ])
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 1,
        event_type: "reflect",
        payload: { strategy_reason_code: "reuse" },
        created_at: "2026-03-14T00:00:03Z",
      },
    ])

    render(<MemoryRouter initialEntries={["/chat"]}><ChatPage /></MemoryRouter>)

    expect(await screen.findByText("检测到可复用场景，是否保存为 Agent？")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "保存为新页面" })).not.toBeInTheDocument()
  })

  it("saves agent in chat and then navigates to agent editor", async () => {
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 12,
        conversation_id: 1,
        role: "assistant",
        content: "这个需求可复用。",
        created_at: "2026-03-14T00:00:02Z",
      },
    ])
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 2,
        event_type: "reflect",
        payload: { strategy_reason_code: "reuse" },
        created_at: "2026-03-14T00:00:03Z",
      },
    ])

    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
          <Route
            path="/agent"
            element={
              <div>
                <div>AGENT_CONSOLE</div>
                <LocationProbe />
              </div>
            }
          />
        </Routes>
      </MemoryRouter>
    )

    const button = await screen.findByRole("button", { name: "保存" })
    await userEvent.click(button)

    await waitFor(() => expect(screen.getByText("前往编辑")).toBeInTheDocument())
    await userEvent.click(screen.getByRole("button", { name: "前往编辑" }))

    await waitFor(() => expect(screen.getByText("AGENT_CONSOLE")).toBeInTheDocument())
    expect(screen.getByTestId("location-probe")).toHaveTextContent(
      "/agent?editAgentId=101"
    )
  })

  it("triggers save-agent flow when user types command in chat input", async () => {
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 13,
        conversation_id: 1,
        role: "assistant",
        content: "这段对话可以沉淀成 Agent。",
        created_at: "2026-03-14T00:00:02Z",
      },
    ])
    chatApi.stream.mockResolvedValueOnce(
      sseResponse([
        'data: {"type":"save_agent_status","data":{"stage":"summarizing_context","message":"总结上下文中..."}}\n\n',
        'data: {"type":"save_agent_status","data":{"stage":"saving_agent","message":"保存 Agent 中..."}}\n\n',
        'data: {"type":"save_agent_done","data":{"agent_id":102,"agent_name":"会话沉淀 Agent","agent_url":"/agent?editAgentId=102","message":"已保存，可跳转到 Agent 编辑页继续修改。"}}\n\n',
        'data: {"type":"done","data":{"trace_id":"trace-2"}}\n\n',
      ])
    )

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText("输入问题...")
    await userEvent.type(input, "保存为 Agent")
    await userEvent.keyboard("{Enter}")

    await waitFor(() => expect(chatApi.stream).toHaveBeenCalled())
    expect(chatApi.saveAgentStream).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText("前往编辑")).toBeInTheDocument())
  })

  it("prefers backend user_message for stream errors", async () => {
    chatApi.stream.mockResolvedValue(
      sseResponse([
        'data: {"type":"error","data":{"message":"technical error","user_message":"请先选择数据源后再继续。","error_class":"runtime_error"}}\n\n',
      ])
    )

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText("输入问题...")
    await userEvent.type(input, "帮我查一下")
    await userEvent.keyboard("{Enter}")

    await waitFor(() =>
      expect(screen.getByText("抱歉，发生了错误：请先选择数据源后再继续。")).toBeInTheDocument()
    )
  })

  it("renders persisted items by actual timestamp within the same turn", async () => {
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 10,
        conversation_id: 1,
        role: "user",
        content: "帮我分析",
        created_at: "2026-03-14T00:00:01Z",
      },
    ])
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 200,
        conversation_id: 1,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01Z",
        payload: { content: "帮我分析", message_id: 10 },
      },
      {
        id: 201,
        conversation_id: 1,
        event_type: "step_result",
        turn_seq: 1,
        part_seq: 1,
        created_at: "2026-03-14T00:00:02Z",
        payload: {
          kind: "tool",
          name: "execute_sql",
          arguments: "{\"sql\":\"select 1\"}",
          result: { success: true, data: { rows: [{ value: 1 }] } },
        },
      },
      {
        id: 202,
        conversation_id: 1,
        event_type: "assistant",
        turn_seq: 1,
        part_seq: 2,
        role: "assistant",
        agent_name: "ChatAgent",
        created_at: "2026-03-14T00:00:03Z",
        payload: { content: "最终结论", event_kind: "assistant_text" },
      },
    ])

    render(
      <MemoryRouter initialEntries={["/chat?conversationId=1"]}>
        <ChatPage />
      </MemoryRouter>
    )

    const toolCard = await screen.findByRole("button", { name: /工具调用：execute_sql/i })
    const finalAnswer = await screen.findByText("最终结论")

    expect(toolCard.compareDocumentPosition(finalAnswer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("hides generic Assistant label in message bubbles", async () => {
    messagesApi.list.mockResolvedValueOnce([
      {
        id: 12,
        conversation_id: 1,
        role: "assistant",
        content: "这是历史结论。",
        agent_name: "Assistant",
        created_at: "2026-03-14T00:00:02Z",
      },
    ])

    render(
      <MemoryRouter initialEntries={["/chat?conversationId=1"]}>
        <ChatPage />
      </MemoryRouter>
    )

    expect(await screen.findByText("这是历史结论。")).toBeInTheDocument()
    expect(screen.queryByText(/^Assistant$/)).not.toBeInTheDocument()
  })

  it("keeps scene conversations out of the default list and renders them as read-only history", async () => {
    conversationsApi.list.mockImplementation(async (params?: { category?: string }) => {
      if (params?.category === "scene") {
        return [
          buildConversation({
            id: 9,
            title: "SQL 分析 · sql-2",
            category: "scene",
            scene_key: "sql_analysis",
            read_only: true,
          }),
        ]
      }
      if (params?.category === "agent_run") return []
      return [buildConversation({ id: 1, title: "普通对话" })]
    })

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    expect(await screen.findByText("普通对话")).toBeInTheDocument()
    expect(await screen.findByText("其他页面历史（只读）")).toBeInTheDocument()
    expect(await screen.findByText("SQL 分析 · sql-2")).toBeInTheDocument()
    expect(await screen.findByText("sql_analysis")).toBeInTheDocument()
    expect(conversationsApi.list).toHaveBeenCalledWith({ category: "primary" })
    expect(conversationsApi.list).toHaveBeenCalledWith({ category: "scene" })
  })

  it("switches scene conversations into read-only mode on ChatPage", async () => {
    conversationsApi.list.mockImplementation(async (params?: { category?: string }) => {
      if (params?.category === "scene") {
        return [
          buildConversation({
            id: 9,
            title: "统计分析历史",
            category: "scene",
            scene_key: "stats_analysis",
            read_only: true,
          }),
        ]
      }
      if (params?.category === "agent_run") return []
      return [buildConversation({ id: 1, title: "普通对话" })]
    })

    const { container } = render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    await screen.findByText("统计分析历史")
    await userEvent.click(screen.getByRole("button", { name: /统计分析历史/ }))

    expect(
      await screen.findByText("该会话来自其他页面的场景历史，只支持查看，不可在 Chat 页面继续对话。")
    ).toBeInTheDocument()
    expect(screen.queryByPlaceholderText("输入问题...")).not.toBeInTheDocument()
    const datasourceButton = Array.from(container.querySelectorAll("button")).find((element) =>
      element.className.includes("max-w-[280px]")
    )
    expect(datasourceButton).toBeDefined()
    expect(datasourceButton as HTMLButtonElement).toBeDisabled()
  })

  it("clears all conversations only after confirmation", async () => {
    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    await screen.findByText("对话1")
    await userEvent.click(screen.getByRole("button", { name: "清空" }))

    expect(await screen.findByText("清空会话")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "清空全部" }))

    await waitFor(() => expect(conversationsApi.delete).toHaveBeenCalledWith(1))
    expect(screen.getByText("暂无会话，点击右上角创建")).toBeInTheDocument()
  })

  it("does not clear conversations when confirmation is canceled", async () => {
    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    )

    await screen.findByText("对话1")
    await userEvent.click(screen.getByRole("button", { name: "清空" }))
    expect(await screen.findByText("清空会话")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "取消" }))

    await waitFor(() => expect(screen.queryByText("清空会话")).not.toBeInTheDocument())
    expect(conversationsApi.delete).not.toHaveBeenCalled()
  })
})
