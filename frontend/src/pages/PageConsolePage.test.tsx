import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"

import { PageConsolePage } from "./PageConsolePage"

const { pagesApi, chatApi, conversationsApi, messagesApi } = vi.hoisted(() => ({
  pagesApi: {
    list: vi.fn(),
    create: vi.fn(),
    listBuildRuns: vi.fn(),
    buildRun: vi.fn(),
    buildRunStream: vi.fn(),
    update: vi.fn(),
    freeze: vi.fn(),
    compile: vi.fn(),
    listSnapshots: vi.fn(),
    listCompileRuns: vi.fn(),
    publish: vi.fn(),
  },
  chatApi: {
    stream: vi.fn(),
    listEvents: vi.fn(),
  },
  conversationsApi: {
    create: vi.fn(),
    list: vi.fn(),
    createBuildSession: vi.fn(),
    heartbeatBuildSession: vi.fn(),
    closeBuildSession: vi.fn(),
  },
  messagesApi: {
    create: vi.fn(),
    list: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  pagesApi,
  chatApi,
  conversationsApi,
  messagesApi,
}))

function createSseResponse(events: Array<Record<string, unknown>>): Response {
  const lines: string[] = []
  for (const event of events) {
    const type = String(event.type ?? "")
    if (type === "assistant") {
      const data = (event.data as Record<string, unknown>) ?? {}
      const text = String(data.text ?? "")
      lines.push(`0:${JSON.stringify(text)}\n`)
    } else {
      lines.push(`2:${JSON.stringify([{ type, data: event.data ?? {} }])}\n`)
      if (type === "done") lines.push(`d:{"finishReason":"stop"}\n`)
    }
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

describe("PageConsolePage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pagesApi.list.mockResolvedValue([
      {
        id: 1,
        name: "Page-1",
        status: "draft",
        draft_payload: {
          version: "page-runtime-v2",
          config: { title: "初始页面", description: "" },
          source: { language: "tsx", code: "export default function Page(){return <main><p>初始内容</p></main>}" },
          runtime: {
            framework: "html",
            preview_html: "<!doctype html><html><body><main><p>初始内容</p></main></body></html>",
          },
          meta: { history: [] },
        },
      },
    ])
    pagesApi.create.mockResolvedValue({
      id: 2,
      name: "Page-2",
      status: "draft",
      draft_payload: {
        version: "page-runtime-v2",
        config: { title: "Page-2", description: "" },
        source: { language: "tsx", code: "" },
        runtime: { framework: "html", preview_html: "<!doctype html><html><body><main></main></body></html>" },
        meta: { history: [] },
      },
    })
    pagesApi.listBuildRuns.mockResolvedValue([])
    chatApi.listEvents.mockResolvedValue([])
    conversationsApi.list.mockResolvedValue([])
    conversationsApi.create.mockResolvedValue({
      id: 801,
      title: "Build Chat",
      datasource_id: null,
      agent_id: null,
      active_skills: [],
      created_at: "2026-03-15T10:00:00Z",
      updated_at: "2026-03-15T10:00:00Z",
    })
    conversationsApi.createBuildSession.mockResolvedValue({
      id: 901,
      conversation_id: 801,
      scope_type: "builder",
      scope_object_type: "page",
      scope_object_id: "1",
      ttl_seconds: 1800,
      heartbeat_at: "2026-03-15T10:00:00Z",
      expires_at: "2026-03-15T10:30:00Z",
      status: "active",
      created_at: "2026-03-15T10:00:00Z",
      updated_at: "2026-03-15T10:00:00Z",
    })
    conversationsApi.heartbeatBuildSession.mockResolvedValue({
      id: 901,
      conversation_id: 801,
      scope_type: "builder",
      scope_object_type: "page",
      scope_object_id: "1",
      ttl_seconds: 1800,
      heartbeat_at: "2026-03-15T10:01:00Z",
      expires_at: "2026-03-15T10:31:00Z",
      status: "active",
      created_at: "2026-03-15T10:00:00Z",
      updated_at: "2026-03-15T10:01:00Z",
    })
    conversationsApi.closeBuildSession.mockResolvedValue({})
    messagesApi.list.mockResolvedValue([])
    messagesApi.create.mockImplementation(async ({ conversation_id, role, content }: Record<string, any>) => ({
      id: Date.now(),
      conversation_id,
      role,
      content,
      created_at: "2026-03-15T10:00:00Z",
    }))
    pagesApi.update.mockResolvedValue({
      id: 1,
      name: "Page-1-Edited",
      draft_payload: {
        version: "page-runtime-v2",
        config: { title: "Page-1-Edited", description: "" },
        source: { language: "tsx", code: "" },
        runtime: { framework: "html", preview_html: "<!doctype html><html><body><main></main></body></html>" },
        meta: { history: [] },
      },
    })
    pagesApi.freeze.mockResolvedValue({ id: 11, page_id: 1 })
    pagesApi.compile.mockResolvedValue({ id: 12, status: "done", summary: "编译完成" })
    pagesApi.listSnapshots.mockResolvedValue([])
    pagesApi.listCompileRuns.mockResolvedValue([])
    pagesApi.publish.mockResolvedValue({
      page: { id: 1, status: "published" },
      release: { id: 7 },
    })
    chatApi.stream.mockResolvedValue(
      createSseResponse([
        { type: "phase", phase: "plan", event_group: "core", event_name: "plan", data: { status: "running", summary: "Plan · 需求分析" } },
        { type: "phase", phase: "act", event_group: "core", event_name: "act", data: { status: "running", summary: "Act · 生成草稿" } },
        { type: "extension", event_group: "extension", event_name: "preview_result", data: { summary: "Preview · 预览已刷新" } },
        { type: "assistant", data: { text: "居中、文本页面" } },
        {
          type: "done",
          data: {
            status: "done",
            page: {
              id: 1,
              name: "Page-1",
              status: "draft",
              draft_payload: {
                version: "page-runtime-v2",
                config: { title: "欢迎页", description: "" },
                source: { language: "tsx", code: "export default function Page(){return <main><p>欢迎使用页面构建模式。</p></main>}" },
                runtime: {
                  framework: "html",
                  preview_html: "<!doctype html><html><body><main><p>欢迎使用页面构建模式。</p></main></body></html>",
                },
                meta: {
                  plan: { goal: "", todos: [] },
                  history: [
                    {
                      prompt: "构建一个欢迎页",
                      summary: "居中、文本页面",
                      created_at: "2026-03-15T10:05:00Z",
                    },
                  ],
                },
              },
            },
            build_summary: "居中、文本页面",
            build_run: {
              run_id: "pbr_1",
              status: "done",
              phase: "preview_ready",
              result_summary: "居中、文本页面",
              events: [
                { phase: "intent_parsed" },
                { phase: "draft_planned" },
                { phase: "plan_generated" },
                { phase: "code_generated" },
                { phase: "patch_validated" },
                { phase: "patch_applied" },
                { phase: "preview_ready" },
              ],
            },
          },
        },
      ])
    )
  })

  it("hydrates chat history from persisted build runs when reopening workspace", async () => {
    pagesApi.listBuildRuns.mockResolvedValue([
      {
        run_id: "pbr_history_1",
        prompt: "点击改为显示罗马数字",
        result_summary: "已改为罗马数字显示",
        error_summary: null,
      },
    ])

    render(
      <MemoryRouter initialEntries={["/page/workspace/1"]}>
        <Routes>
          <Route path="/page/workspace/:pageId" element={<PageConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText("点击改为显示罗马数字")).toBeInTheDocument()
    expect(screen.getByText("已改为罗马数字显示")).toBeInTheDocument()
  })

  it("redirects to page list when entry route is /page/workspace without id", async () => {
    render(
      <MemoryRouter initialEntries={["/page/workspace"]}>
        <Routes>
          <Route
            path="/page/workspace/:pageId"
            element={
              <div>
                <PageConsolePage />
                <LocationProbe />
              </div>
            }
          />
          <Route
            path="/page/workspace"
            element={
              <div>
                <PageConsolePage />
                <LocationProbe />
              </div>
            }
          />
          <Route path="/page" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() =>
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/page")
    )
  })

  it("renders single workspace with preview and chat composer", async () => {
    render(
      <MemoryRouter initialEntries={["/page/workspace/1"]}>
        <Routes>
          <Route path="/page/workspace/:pageId" element={<PageConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByTitle("Page Runtime Preview")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("描述你要构建的页面，我会逐步生成并更新预览")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "切换到 View 模式" })).not.toBeInTheDocument()
  })

  it("builds page via unified scene chat ingress and updates preview", async () => {
    render(
      <MemoryRouter initialEntries={["/page/workspace/1"]}>
        <Routes>
          <Route path="/page/workspace/:pageId" element={<PageConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText("描述你要构建的页面，我会逐步生成并更新预览")
    await userEvent.type(input, "构建一个欢迎页")
    await userEvent.keyboard("{Enter}")

    await waitFor(() =>
      expect(chatApi.stream).toHaveBeenCalledWith(
        801,
        "构建一个欢迎页",
        expect.objectContaining({
          sceneAgent: expect.objectContaining({
            key: "page_build",
            context: expect.objectContaining({ page_id: 1 }),
            focus_object: expect.objectContaining({ kind: "page", page_id: 1 }),
          }),
          conversationContext: expect.stringContaining("user: 构建一个欢迎页"),
        })
      )
    )
    expect(screen.getByText("构建一个欢迎页")).toBeInTheDocument()
    const previewFrame = screen.getByTitle("Page Runtime Preview")
    await waitFor(() =>
      expect(previewFrame.getAttribute("srcdoc")).toContain("欢迎使用页面构建模式")
    )
  })

  it("prefers diff summary when build summary is too generic", async () => {
    chatApi.stream.mockResolvedValueOnce(
      createSseResponse([
        { type: "phase", phase: "act", event_group: "core", event_name: "act", data: { status: "running", summary: "Act · 正在更新页面" } },
        {
          type: "done",
          data: {
            status: "done",
            page: {
              id: 1,
              name: "Page-1",
              status: "draft",
              draft_payload: {
                version: "page-runtime-v2",
                config: { title: "罗马数字页", description: "" },
                source: { language: "tsx", code: "export default function Page(){return <main><p>I</p></main>}" },
                runtime: {
                  framework: "html",
                  preview_html: "<!doctype html><html><body><main><p>I</p></main></body></html>",
                },
                meta: { history: [] },
              },
            },
            build_summary: "页面已更新",
            build_run: {
              run_id: "pbr_observe_1",
              status: "done",
              phase: "apply",
              result_summary: "页面已更新",
              events: [
                {
                  phase: "apply",
                  payload: {
                    changed_files: ["main.tsx", "preview.html"],
                    diff_summary: "已将点击结果从字母序列改为罗马数字序列。",
                    tests_suggested: ["连续点击 5 次应显示 I, II, III, IV, V"],
                    risk_notes: ["如果存在旧缓存，请刷新后重试"],
                  },
                },
              ],
            },
          },
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={["/page/workspace/1"]}>
        <Routes>
          <Route path="/page/workspace/:pageId" element={<PageConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText("描述你要构建的页面，我会逐步生成并更新预览")
    await userEvent.type(input, "点击改为显示罗马数字")
    await userEvent.keyboard("{Enter}")

    expect(await screen.findByText("已将点击结果从字母序列改为罗马数字序列。")).toBeInTheDocument()
  })

  it("does not append duplicate assistant summary in one run", async () => {
    chatApi.stream.mockResolvedValueOnce(
      createSseResponse([
        { type: "phase", phase: "plan", event_group: "core", event_name: "plan", data: { status: "running", summary: "Plan · 需求分析" } },
        { type: "phase", phase: "act", event_group: "core", event_name: "act", data: { status: "running", summary: "Act · 更新草稿" } },
        { type: "assistant", data: { text: "页面已更新：新增筛选和趋势图。" } },
        {
          type: "done",
          data: {
            status: "done",
            page: {
              id: 1,
              name: "Page-1",
              status: "draft",
              draft_payload: {
                version: "page-runtime-v2",
                config: { title: "诊断总览", description: "" },
                source: { language: "tsx", code: "export default function Page(){return <main><p>诊断总览</p></main>}" },
                runtime: {
                  framework: "html",
                  preview_html: "<!doctype html><html><body><main><p>诊断总览</p></main></body></html>",
                },
                meta: { history: [] },
              },
            },
            build_summary: "页面已更新：新增筛选和趋势图。",
            build_run: {
              run_id: "pbr_no_duplicate",
              status: "done",
              phase: "apply",
              result_summary: "页面已更新：新增筛选和趋势图。",
              events: [{ phase: "apply", payload: {} }],
            },
          },
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={["/page/workspace/1"]}>
        <Routes>
          <Route path="/page/workspace/:pageId" element={<PageConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText("描述你要构建的页面，我会逐步生成并更新预览")
    await userEvent.type(input, "生成数据库诊断总览")
    await userEvent.keyboard("{Enter}")

    await waitFor(() => expect(chatApi.stream).toHaveBeenCalled())
    const finalAssistantMessages = await screen.findAllByText("页面已更新：新增筛选和趋势图。")
    expect(finalAssistantMessages).toHaveLength(1)
  })

  it("supports publish from workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/page/workspace/1"]}>
        <Routes>
          <Route
            path="/page/workspace/:pageId"
            element={
              <div>
                <PageConsolePage />
                <LocationProbe />
              </div>
            }
          />
          <Route path="/page/:pageId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByTitle("Page Runtime Preview")
    await userEvent.click(screen.getByRole("button", { name: "发布" }))
    await waitFor(() => expect(pagesApi.freeze).toHaveBeenCalledWith(1, expect.any(Object)))
    await waitFor(() => expect(pagesApi.compile).toHaveBeenCalledWith(1, expect.any(Object)))
    await waitFor(() => expect(pagesApi.publish).toHaveBeenCalledWith(1))
    await waitFor(() =>
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/page/1")
    )
  })
})
