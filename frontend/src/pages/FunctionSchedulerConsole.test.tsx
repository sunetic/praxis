import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"

import { FunctionBuildPage } from "./FunctionBuildPage"
import { FunctionListPage } from "./FunctionListPage"
import { SchedulerConsolePage } from "./SchedulerConsolePage"

const { functionsApi, schedulesApi, datasourcesApi, agentsApi, chatApi, conversationsApi, messagesApi } = vi.hoisted(() => ({
  functionsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    listBuildRuns: vi.fn(),
    listAllRuns: vi.fn(),
    buildChatStream: vi.fn(),
    buildChat: vi.fn(),
    build: vi.fn(),
    release: vi.fn(),
    suggestInput: vi.fn(),
    invoke: vi.fn(),
  },
  schedulesApi: {
    list: vi.fn(),
    workerHealth: vi.fn(),
    listRuns: vi.fn(),
    listRunsPage: vi.fn(),
    listAllRunsPage: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    aiCreate: vi.fn(),
    build: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    disable: vi.fn(),
    enable: vi.fn(),
    runNow: vi.fn(),
    repairRun: vi.fn(),
  },
  agentsApi: {
    list: vi.fn(),
  },
  datasourcesApi: {
    list: vi.fn(),
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
  functionsApi,
  schedulesApi,
  agentsApi,
  datasourcesApi,
  chatApi,
  conversationsApi,
  messagesApi,
}))

function createSseResponse(events: Array<Record<string, any>>): Response {
  const lines: string[] = []
  for (const event of events) {
    const type = String(event.type ?? "")
    if (type === "assistant") {
      const text = String(event.data?.text ?? "")
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

describe("Function and Scheduler consoles", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    functionsApi.list.mockResolvedValue([
      { id: 1, name: "daily-report", status: "draft", draft_code: "result = {'ok': True}" },
      { id: 2, name: "slow-sql-analyzer", status: "released", draft_code: "result = {'rows': []}" },
    ])
    functionsApi.get.mockResolvedValue({
      id: 1,
      name: "daily-report",
      description: "由 Function 控制台创建",
      status: "draft",
      draft_code: "result = {'ok': True}",
      draft_dependencies: null,
    })
    functionsApi.invoke.mockResolvedValue({
      status: "success",
      duration_ms: 18,
      run_id: "invoke-default-1",
      output: { ok: true },
      error_message: null,
      error_code: null,
      runtime_path: "production",
    })
    functionsApi.create.mockResolvedValue({
      id: 3,
      name: "未命名 Function a1b2c3",
      slug: "fn-new",
      status: "draft",
      description: "",
      draft_code: "result = {'ok': True}",
      draft_dependencies: null,
    })
    functionsApi.update.mockImplementation(async (id: number, payload: Record<string, any>) => ({
      id,
      name: payload.name || "daily-report",
      description: payload.description || "由 Function 控制台创建",
      status: "draft",
    }))
    functionsApi.delete.mockResolvedValue({})
    functionsApi.listBuildRuns.mockResolvedValue([])
    functionsApi.listAllRuns.mockResolvedValue([])
    chatApi.listEvents.mockResolvedValue([])
    conversationsApi.list.mockResolvedValue([])
    conversationsApi.create.mockResolvedValue({
      id: 501,
      title: "Build Chat",
      datasource_id: null,
      agent_id: null,
      active_skills: [],
      created_at: "2026-03-14T12:00:00Z",
      updated_at: "2026-03-14T12:00:00Z",
    })
    conversationsApi.createBuildSession.mockResolvedValue({
      id: 601,
      conversation_id: 501,
      scope_type: "builder",
      scope_object_type: "function",
      scope_object_id: "1",
      ttl_seconds: 1800,
      heartbeat_at: "2026-03-14T12:00:00Z",
      expires_at: "2026-03-14T12:30:00Z",
      status: "active",
      created_at: "2026-03-14T12:00:00Z",
      updated_at: "2026-03-14T12:00:00Z",
    })
    conversationsApi.heartbeatBuildSession.mockResolvedValue({
      id: 601,
      conversation_id: 501,
      scope_type: "builder",
      scope_object_type: "function",
      scope_object_id: "1",
      ttl_seconds: 1800,
      heartbeat_at: "2026-03-14T12:01:00Z",
      expires_at: "2026-03-14T12:31:00Z",
      status: "active",
      created_at: "2026-03-14T12:00:00Z",
      updated_at: "2026-03-14T12:01:00Z",
    })
    conversationsApi.closeBuildSession.mockResolvedValue({})
    messagesApi.list.mockResolvedValue([])
    messagesApi.create.mockImplementation(async ({ conversation_id, role, content }: Record<string, any>) => ({
      id: Date.now(),
      conversation_id,
      role,
      content,
      created_at: "2026-03-14T12:00:00Z",
    }))
    chatApi.stream.mockImplementation(async (_conversationId: number, content: string) =>
      createSseResponse([
        {
          type: "phase",
          phase: "plan",
          data: { status: "running", summary: `Plan · 已解析 ${content}。` },
        },
        {
          type: "phase",
          phase: "act",
          data: { status: "running", summary: "Act · Function 草稿已生成。" },
        },
        {
          type: "phase",
          phase: "observe",
          data: { status: "done", summary: "Observe · Function 草稿校验通过。" },
        },
        {
          type: "assistant",
          data: { text: "带运行标识的函数" },
        },
        {
          type: "done",
          data: {
            action: "build",
            status: "done",
            assistant_message: "带运行标识的函数",
            function: {
              id: 1,
              name: "daily-report",
              description: "带运行标识的函数",
              status: "draft",
              draft_dependencies: null,
              draft_code: "result = {'ok': True, 'run_id': context.get('trace_id')}",
            },
          },
        },
      ])
    )
    functionsApi.release.mockResolvedValue({
      function: {
        id: 1,
        name: "daily-report",
        description: "由 Function 控制台创建",
        status: "released",
      },
      release: { version: 1 },
    })
    functionsApi.buildChatStream.mockImplementation(async (_id: number, data: Record<string, any>) => {
      const action = String(data?.action || "build")
      if (action === "suggest_input") {
        return createSseResponse([
          { type: "phase", phase: "suggest_input", data: { status: "done", summary: "测试入参建议已生成。" } },
          { type: "assistant", data: { text: "已生成测试入参" } },
          {
            type: "done",
            data: {
              action: "suggest_input",
              status: "done",
              assistant_message: "已生成测试入参",
              suggestion: {
                payload: { rows: [1, 2, 3] },
                rationale: "已生成测试入参",
                missing_information: [],
                assumptions: [],
              },
            },
          },
        ])
      }
      if (action === "invoke") {
        return createSseResponse([
          { type: "phase", phase: "invoke_finished", data: { status: "success", summary: "测试已执行成功。" } },
          { type: "assistant", data: { text: "测试已执行成功。" } },
          {
            type: "done",
            data: {
              action: "invoke",
              status: "success",
              assistant_message: "测试已执行成功。",
              duration_ms: 12,
              run_id: "run-1",
              output: { rows: 3 },
            },
          },
        ])
      }
      return createSseResponse([
        {
          type: "phase",
          phase: "plan",
          data: { status: "running", summary: "Plan · 已解析 Function 需求。" },
        },
        {
          type: "phase",
          phase: "act",
          data: { status: "running", summary: "Act · Function 草稿已生成。" },
        },
        {
          type: "phase",
          phase: "observe",
          data: { status: "done", summary: "Observe · Function 草稿校验通过。" },
        },
        {
          type: "assistant",
          data: { text: "带运行标识的函数" },
        },
        {
          type: "done",
          data: {
            action: "build",
            status: "done",
            assistant_message: "带运行标识的函数",
            function: {
              id: 1,
              name: "daily-report",
              description: "带运行标识的函数",
              status: "draft",
              draft_dependencies: null,
              draft_code: "result = {'ok': True, 'run_id': context.get('trace_id')}",
            },
          },
        },
      ])
    })
    schedulesApi.list.mockResolvedValue([
      {
        id: 100,
        name: "daily-job",
        status: "active",
        target_type: "function",
        target_id: 1,
        schedule_type: "interval",
        interval_seconds: 60,
        timezone: "UTC",
        max_retries: 1,
        retry_backoff_seconds: 30,
        function_id: 1,
        input_payload: null,
        input_prompt: null,
        next_run_at: "2026-03-14T12:00:00Z",
      },
    ])
    schedulesApi.listRuns.mockResolvedValue([
      {
        id: 1,
        schedule_id: 100,
        run_id: "run-1",
        status: "success",
        trigger_type: "manual",
        attempt: 1,
        retry_count: 0,
        max_retries: 1,
        output_summary: "ok",
        output_payload: { ok: true },
        started_at: "2026-03-14T11:58:00Z",
        finished_at: "2026-03-14T11:58:01Z",
        created_at: "2026-03-14T11:58:01Z",
      },
    ])
    schedulesApi.listRunsPage.mockResolvedValue({
      items: [
        {
          id: 1,
          schedule_id: 100,
          run_id: "run-1",
          status: "success",
          trigger_type: "manual",
          attempt: 1,
          retry_count: 0,
          max_retries: 1,
          output_summary: "ok",
          output_payload: { ok: true },
          started_at: "2026-03-14T11:58:00Z",
          finished_at: "2026-03-14T11:58:01Z",
          created_at: "2026-03-14T11:58:01Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    })
    schedulesApi.listAllRunsPage.mockResolvedValue({
      items: [
        {
          id: 1,
          schedule_id: 100,
          run_id: "run-1",
          status: "success",
          trigger_type: "manual",
          attempt: 1,
          retry_count: 0,
          max_retries: 1,
          output_summary: "ok",
          output_payload: { ok: true },
          started_at: "2026-03-14T11:58:00Z",
          finished_at: "2026-03-14T11:58:01Z",
          created_at: "2026-03-14T11:58:01Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    })
    schedulesApi.create.mockResolvedValue({ id: 101, name: "new-job" })
    schedulesApi.workerHealth.mockResolvedValue({ running: true, shutting_down: false, job_count: 1, autostart: true })
    schedulesApi.update.mockResolvedValue({ id: 100, status: "active", function_id: 1 })
    schedulesApi.delete.mockResolvedValue({})
    schedulesApi.aiCreate.mockResolvedValue({
      schedule: {
        id: 101,
        name: "ai-job",
        status: "active",
        target_type: "function",
        target_id: 2,
        schedule_type: "interval",
        interval_seconds: 300,
      },
      build_summary: "间隔调度：每 300 秒执行",
    })
    schedulesApi.build.mockResolvedValue({
      schedule: {
        id: 100,
        name: "daily-job",
        status: "active",
        target_type: "function",
        target_id: 1,
        schedule_type: "interval",
        interval_seconds: 300,
        function_id: 1,
        next_run_at: "2026-03-14T12:05:00Z",
        max_retries: 3,
        timezone: "UTC",
      },
      build_summary: "间隔调度：每 300 秒执行",
    })
    schedulesApi.pause.mockResolvedValue({ id: 100, status: "paused", function_id: 1 })
    schedulesApi.resume.mockResolvedValue({ id: 100, status: "active", function_id: 1 })
    schedulesApi.disable.mockResolvedValue({ id: 100, status: "paused", function_id: 1 })
    schedulesApi.enable.mockResolvedValue({ id: 100, status: "active", function_id: 1 })
    schedulesApi.runNow.mockResolvedValue({ schedule_id: 100, run_id: "run-1" })
    schedulesApi.repairRun.mockResolvedValue({
      id: 1,
      schedule_id: 100,
      run_id: "run-1",
      status: "failed",
      runtime_status: "failed",
      trigger_type: "manual",
      attempt: 1,
      retry_count: 0,
      max_retries: 1,
      error_summary: "Manually repaired stale running schedule run",
      output_summary: "ok",
      output_payload: { ok: true },
      started_at: "2026-03-14T11:58:00Z",
      finished_at: "2026-03-14T11:59:00Z",
      created_at: "2026-03-14T11:58:01Z",
    })
    agentsApi.list.mockResolvedValue([
      {
        id: 7,
        name: "ops-agent",
        status: "active",
        prompt: "you are ops",
        datasource_ids: [],
      },
    ])
    datasourcesApi.list.mockResolvedValue([
      { id: 1, name: "user-a", tenant_role: "user", status: "active" },
      { id: 2, name: "sys-a", tenant_role: "sys", status: "active" },
      { id: 3, name: "disabled", tenant_role: "user", status: "inactive" },
    ])
  })

  it("shows function list in table mode", async () => {
    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByRole("button", { name: "新建" })).toBeInTheDocument()
    expect(screen.getByText("ID")).toBeInTheDocument()
    expect(screen.getByText("#1")).toBeInTheDocument()
    expect(screen.getByText("daily-report")).toBeInTheDocument()
  })

  it("uses a fixed action group and truncation-friendly metadata cells in function list", async () => {
    const longDescription = "这是一个很长很长的描述，用来验证描述列不会继续无限扩张并把操作列挤成多行。"
    functionsApi.list.mockResolvedValueOnce([
      {
        id: 9,
        name: "extremely-verbose-function-name",
        status: "released",
        description: longDescription,
        updated_at: "2026-03-14 12:00:00",
      },
    ])

    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByRole("group", { name: "Function 操作 extremely-verbose-function-name" })).toBeInTheDocument()
    expect(screen.getByTitle(longDescription)).toBeInTheDocument()
    expect(screen.getByTitle("2026-03-14 12:00:00")).toBeInTheDocument()
  })

  it("keeps duplicate function rows distinguishable during delete confirmation", async () => {
    functionsApi.list.mockResolvedValueOnce([
      {
        id: 15,
        name: "tenant-health-check",
        slug: "tenant-health-check-a",
        status: "draft",
        description: "same-desc",
        updated_at: "2026-03-27 20:00:00",
      },
      {
        id: 16,
        name: "tenant-health-check",
        slug: "tenant-health-check-b",
        status: "draft",
        description: "same-desc",
        updated_at: "2026-03-27 20:01:00",
      },
    ])

    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findAllByText("tenant-health-check")).toHaveLength(2)
    expect(screen.getByText("#15 · tenant-health-check-a")).toBeInTheDocument()
    expect(screen.getByText("#16 · tenant-health-check-b")).toBeInTheDocument()

    await userEvent.click(screen.getAllByRole("button", { name: "删除 tenant-health-check" })[0])

    expect(screen.getByText("目标标识：#15 · tenant-health-check-a")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "删除" }))

    expect(functionsApi.delete).toHaveBeenCalledWith(15)
    await waitFor(() => expect(screen.queryByText("#15 · tenant-health-check-a")).not.toBeInTheDocument())
    expect(screen.getByText("#16 · tenant-health-check-b")).toBeInTheDocument()
    expect(screen.getAllByText("tenant-health-check")).toHaveLength(1)
  })

  it("builds function in dedicated build workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText("Build Chat")).toBeInTheDocument()
    expect(screen.getByText(/Build 内只做预演/)).toBeInTheDocument()
    await userEvent.type(
      screen.getByPlaceholderText("例如：读取业务租户后，输出 tenant_name 与 database_list"),
      "增加结果字段 run_id"
    )
    await userEvent.keyboard("{Enter}")

    expect((await screen.findAllByText("带运行标识的函数")).length).toBeGreaterThan(0)
    expect(chatApi.stream).toHaveBeenCalledWith(
      501,
      "增加结果字段 run_id",
      expect.objectContaining({
        sceneAgent: expect.objectContaining({
          key: "function_build",
          context: expect.objectContaining({ function_id: 1 }),
          focus_object: expect.objectContaining({ kind: "function", function_id: 1 }),
        }),
        conversationContext: expect.stringContaining("增加结果字段 run_id"),
      })
    )
  })

  it("uses inline title edit in build workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    expect(screen.queryByRole("button", { name: "保存标题" })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "daily-report" }))
    const titleInput = screen.getByLabelText("Function 名称")
    await userEvent.clear(titleInput)
    await userEvent.type(titleInput, "daily-report-v3")
    await userEvent.keyboard("{Enter}")

    expect(functionsApi.update).toHaveBeenCalledWith(1, expect.objectContaining({ name: "daily-report-v3" }))
  })

  it("uses inline description edit in build workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.click(screen.getByRole("button", { name: /添加描述|由 Function 控制台创建/i }))
    const descriptionInput = screen.getByLabelText("Function 描述")
    await userEvent.clear(descriptionInput)
    await userEvent.type(descriptionInput, "新的描述")
    await userEvent.keyboard("{Enter}")

    expect(functionsApi.update).toHaveBeenCalledWith(1, expect.objectContaining({ description: "新的描述" }))
    expect(functionsApi.update).not.toHaveBeenCalledWith(1, expect.objectContaining({ name: "daily-report" }))
  })

  it("shows save-draft and release actions in build workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    expect(screen.getByText("Functions")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "发布" })).toBeInTheDocument()
  })

  it("navigates back to function list from build workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route
            path="/function/:functionId/build"
            element={
              <>
                <FunctionBuildPage />
                <LocationProbe />
              </>
            }
          />
          <Route path="/function" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.click(screen.getByText("Functions"))

    await waitFor(() => expect(screen.getByTestId("location-probe")).toHaveTextContent("/function"))
  })

  it("releases current function from build workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.click(screen.getByRole("button", { name: "发布" }))

    expect(functionsApi.release).toHaveBeenCalledWith(1, {})
    expect(await screen.findByRole("button", { name: "已发布" })).toBeDisabled()
  })

  it("returns a released function to draft after saving a new edit", async () => {
    functionsApi.get.mockResolvedValueOnce({
      id: 1,
      name: "daily-report",
      description: "已发布版本",
      status: "released",
      draft_code: "result = {'ok': True}",
      draft_dependencies: null,
    })
    functionsApi.update.mockResolvedValueOnce({
      id: 1,
      name: "daily-report-v2",
      description: "已发布版本",
      status: "draft",
      draft_code: "result = {'ok': True}",
      draft_dependencies: null,
    })

    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    expect(screen.getByRole("button", { name: "已发布" })).toBeDisabled()

    await userEvent.click(screen.getByRole("button", { name: "daily-report" }))
    const titleInput = screen.getByLabelText("Function 名称")
    await userEvent.clear(titleInput)
    await userEvent.type(titleInput, "daily-report-v2")
    await userEvent.keyboard("{Enter}")

    expect(await screen.findByRole("button", { name: "发布" })).toBeEnabled()
  })

  it("renders build assistant_message as the single source of truth", async () => {
    chatApi.stream.mockImplementationOnce(async () =>
      createSseResponse([
        { type: "phase", phase: "act", data: { status: "running", summary: "Function 草稿已生成。" } },
        { type: "assistant", data: { text: "根据给定的数据源 ID，查询该数据源下包含的数据库列表。" } },
        {
          type: "done",
          data: {
            status: "done",
            assistant_message: "根据给定的数据源 ID，查询该数据源下包含的数据库列表。",
            function: {
              id: 1,
              name: "daily-report",
              description: "由 Function 控制台创建",
              status: "draft",
              draft_code: "result = {'ok': True}",
            },
          },
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.type(
      screen.getByPlaceholderText("例如：读取业务租户后，输出 tenant_name 与 database_list"),
      "给定 datasource 的id，给出 datasource 的 database 列表"
    )
    await userEvent.keyboard("{Enter}")

    await waitFor(() => {
      expect(screen.getByText("根据给定的数据源 ID，查询该数据源下包含的数据库列表。")).toBeInTheDocument()
    }, { timeout: 3000 })
    expect(screen.queryByText("请求已提交，等待 Function 构建...")).not.toBeInTheDocument()
  })

  it("does not render duplicate bubble when apply event summary equals assistant_message", async () => {
    chatApi.stream.mockImplementationOnce(async () =>
      createSseResponse([
        { type: "phase", phase: "act", data: { status: "done", summary: "已修复数据库名称提取逻辑。" } },
        { type: "assistant", data: { text: "已修复数据库名称提取逻辑。" } },
        {
          type: "done",
          data: {
            status: "done",
            assistant_message: "已修复数据库名称提取逻辑。",
            function: {
              id: 1,
              name: "daily-report",
              description: "由 Function 控制台创建",
              status: "draft",
              draft_code: "result = {'ok': True}",
            },
          },
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.type(
      screen.getByPlaceholderText("例如：读取业务租户后，输出 tenant_name 与 database_list"),
      "再次修复"
    )
    await userEvent.keyboard("{Enter}")

    await waitFor(() => {
      expect(screen.getAllByText("已修复数据库名称提取逻辑。").length).toBe(1)
    }, { timeout: 3000 })
  })

  it("does not append extra clarification bubble when assistant_message exists", async () => {
    chatApi.stream.mockImplementationOnce(async () =>
      createSseResponse([
        { type: "phase", phase: "plan", data: { status: "running", summary: "已解析 Function 需求。" } },
        { type: "phase", phase: "plan", data: { status: "noted", summary: "检测到影响行为的歧义，需先澄清再生成。" } },
        { type: "assistant", data: { text: "已生成一版 Function。输入参数 datasource_id:integer。可直接测试。" } },
        {
          type: "done",
          data: {
            status: "done",
            assistant_message: "已生成一版 Function。输入参数 datasource_id:integer。可直接测试。",
            function: {
              id: 1,
              name: "daily-report",
              description: "由 Function 控制台创建",
              status: "draft",
              draft_code: "result = {'ok': True}",
            },
          },
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.type(
      screen.getByPlaceholderText("例如：读取业务租户后，输出 tenant_name 与 database_list"),
      "检索数据源，从中找到第一个租户，查询里面所有库"
    )
    await userEvent.keyboard("{Enter}")

    await waitFor(() => {
      expect(screen.getByText("已生成一版 Function。输入参数 datasource_id:integer。可直接测试。")).toBeInTheDocument()
    }, { timeout: 3000 })
    expect(screen.queryByText(/请确认“第一个”按名称升序是否符合你的预期/)).not.toBeInTheDocument()
    expect(chatApi.stream).toHaveBeenCalledWith(
      501,
      "检索数据源，从中找到第一个租户，查询里面所有库",
      expect.objectContaining({
        sceneAgent: expect.objectContaining({
          key: "function_build",
          context: expect.objectContaining({ function_id: 1 }),
          focus_object: expect.objectContaining({ kind: "function", function_id: 1 }),
        }),
        conversationContext: expect.stringContaining("检索数据源，从中找到第一个租户，查询里面所有库"),
      })
    )
  })

  it("keeps scheduler console focused on lifecycle + execution drawer", async () => {
    render(
      <MemoryRouter initialEntries={["/scheduler/100"]}>
        <Routes>
          <Route path="/scheduler/:schedulerId" element={<SchedulerConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByRole("button", { name: "新建 Scheduler" })).toBeInTheDocument()
    expect(await screen.findByRole("button", { name: "daily-job#100" })).toBeInTheDocument()
    expect(screen.queryByText("Page 绑定")).not.toBeInTheDocument()
    expect(screen.queryByRole("heading", { name: "执行详情" })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole("tab", { name: "执行记录" }))
    await userEvent.click(await screen.findByText("ok"))
    expect(await screen.findByRole("heading", { name: "执行详情" })).toBeInTheDocument()
    expect(screen.getByText("执行 ID: run-1")).toBeInTheDocument()
    expect(screen.getByText("输入参数 JSON")).toBeInTheDocument()
    expect(screen.getByText("输出内容")).toBeInTheDocument()
    expect(screen.getByText("调度状态")).toBeInTheDocument()
    expect(screen.getByText("运行态状态")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "关闭" }))

    await userEvent.click(screen.getByRole("tab", { name: "调度列表" }))
    await userEvent.click(screen.getByRole("button", { name: "编辑调度 daily-job" }))
    await userEvent.type(
      screen.getByPlaceholderText("例如：改为每 10 分钟执行，失败重试 2 次，切换上海时区"),
      "改成每 5 分钟执行一次，失败重试 3 次"
    )
    await userEvent.click(screen.getByRole("button", { name: "AI 调整当前 Scheduler" }))

    expect(schedulesApi.build).toHaveBeenCalledWith(100, "改成每 5 分钟执行一次，失败重试 3 次")
  })

  it("repairs stale running runs from the execution drawer", async () => {
    schedulesApi.listAllRunsPage.mockResolvedValueOnce({
      items: [
        {
          id: 1,
          schedule_id: 100,
          run_id: "run-1",
          status: "running",
          runtime_status: null,
          trigger_type: "manual",
          attempt: 1,
          retry_count: 0,
          max_retries: 1,
          output_summary: null,
          output_payload: null,
          error_summary: null,
          started_at: "2026-03-14T11:58:00Z",
          finished_at: null,
          created_at: "2026-03-14T11:58:01Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    })

    render(
      <MemoryRouter initialEntries={["/scheduler/100"]}>
        <Routes>
          <Route path="/scheduler/:schedulerId" element={<SchedulerConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    await userEvent.click(screen.getByRole("tab", { name: "执行记录" }))
    await userEvent.click(await screen.findByText("run-1"))
    expect(await screen.findByRole("button", { name: "修复假 running" })).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "修复假 running" }))

    await waitFor(() => expect(schedulesApi.repairRun).toHaveBeenCalledWith(100, 1))
    expect(await screen.findByText("状态: failed")).toBeInTheDocument()
    expect(screen.getAllByText("Manually repaired stale running schedule run").length).toBeGreaterThan(0)
  })

  it("shows failure message when invoke returns failed status", async () => {
    functionsApi.buildChatStream.mockImplementationOnce(async () =>
      createSseResponse([
        { type: "phase", phase: "invoke_finished", data: { status: "failed", summary: "测试执行失败：未发布版本禁止 production 执行" } },
        { type: "assistant", data: { text: "测试执行失败：未发布版本禁止 production 执行" } },
        {
          type: "done",
          data: {
            action: "invoke",
            status: "failed",
            assistant_message: "测试执行失败：未发布版本禁止 production 执行",
            error_message: "未发布版本禁止 production 执行",
            error_code: "release_required",
            duration_ms: 21,
            run_id: "run-failed-1",
            output: null,
          },
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.click(screen.getByRole("button", { name: "确认并预演" }))

    expect(await screen.findByText(/测试执行失败：当前服务仍在生产路径执行/)).toBeInTheDocument()
    expect(screen.queryByText(/测试已执行成功/)).not.toBeInTheDocument()
  })

  it("opens a right execution drawer and removes the left run console", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    expect(screen.queryByText("Run Console")).not.toBeInTheDocument()
    expect(screen.queryByText("Step 3")).not.toBeInTheDocument()
    expect(screen.getByText(/Build 内只做预演/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "确认并预演" }))

    expect(await screen.findByRole("heading", { name: "执行结果" })).toBeInTheDocument()
    expect(screen.getByText("入参 JSON")).toBeInTheDocument()
    expect(screen.getByText("执行输出")).toBeInTheDocument()
    expect(screen.getByText(/"rows": 3/)).toBeInTheDocument()
  })

  it("passes datasource_id from selected datasource into invoke request", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.click(screen.getByRole("button", { name: "选择测试数据源" }))
    await userEvent.click(screen.getByRole("option", { name: /sys-a/i }))
    await userEvent.click(screen.getByRole("button", { name: "确认并预演" }))

    expect(functionsApi.buildChatStream).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        action: "invoke",
        invoke: expect.objectContaining({
          datasource_id: 2,
          payload: expect.objectContaining({
            datasource_id: 2,
            datasource_ids: [2],
          }),
          write_mode: "readonly",
          execution_mode: "plan",
          runtime_path: "draft",
        }),
      })
    )
  })

  it("keeps invoke datasource_id empty when no datasource is selected", async () => {
    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.click(screen.getByRole("button", { name: "确认并预演" }))

    expect(functionsApi.buildChatStream).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        action: "invoke",
        invoke: expect.not.objectContaining({
          datasource_id: expect.any(Number),
        }),
      })
    )
    const invokeCall = functionsApi.buildChatStream.mock.calls.at(-1)?.[1] as any
    expect(invokeCall?.invoke?.payload?.datasource_id).toBeUndefined()
    expect(invokeCall?.invoke?.payload?.datasource_ids).toBeUndefined()
  })

  it("maps sql syntax error to friendly message", async () => {
    functionsApi.buildChatStream.mockImplementationOnce(async () =>
      createSseResponse([
        { type: "phase", phase: "invoke_finished", data: { status: "failed", summary: "测试执行失败：(1064...)" } },
        { type: "assistant", data: { text: "测试执行失败：(1064...)" } },
        {
          type: "done",
          data: {
            action: "invoke",
            status: "failed",
            assistant_message: "测试执行失败：(1064...)",
            error_message: "数据库执行失败",
            error_code: "sql_syntax_error",
            duration_ms: 12,
            run_id: "run-sql-err-1",
            output: null,
          },
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={["/function/1/build"]}>
        <Routes>
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("Build Chat")
    await userEvent.click(screen.getByRole("button", { name: "确认并预演" }))
    expect((await screen.findAllByText(/生成的 SQL 语法有问题/)).length).toBeGreaterThan(0)
  })

  it("creates function from empty state entry", async () => {
    functionsApi.list.mockResolvedValueOnce([])
    functionsApi.get.mockResolvedValueOnce({
      id: 3,
      name: "未命名 Function a1b2c3",
      description: "",
      status: "draft",
      draft_code: "result = {'ok': True}",
      draft_dependencies: null,
    })

    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    const createBtn = await screen.findByRole("button", { name: "新建 Function" })
    await userEvent.click(createBtn)

    expect(functionsApi.create).toHaveBeenCalledWith({})
    expect(await screen.findByText("Build Chat")).toBeInTheDocument()
  })

  it("navigates to build workspace from the list edit action", async () => {
    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
          <Route path="/function/:functionId/build" element={<FunctionBuildPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("daily-report")
    await userEvent.click(screen.getByRole("button", { name: "编辑 daily-report" }))
    expect(await screen.findByText("Build Chat")).toBeInTheDocument()
  })

  it("uses apply invoke for write-type builtin functions from the list page without rendering generic confirmation UI", async () => {
    functionsApi.list.mockResolvedValueOnce([
      { id: 7, name: "OCP 集群租户导入", slug: "fn-ocp-import", kind: "built_in", status: "released" },
    ])
    functionsApi.get.mockResolvedValueOnce({
      id: 7,
      name: "OCP 集群租户导入",
      slug: "fn-ocp-import",
      kind: "built_in",
      status: "released",
      draft_code: "def main(payload, context):\n    return {}\n",
      draft_dependencies: {
        invoke: {
          mode: "write_apply",
          requires_confirmation: true,
          result_mode: "output",
        },
      },
    })
    functionsApi.invoke.mockResolvedValueOnce({
      status: "success",
      duration_ms: 42,
      run_id: "invoke-ocp-1",
      output: { summary: "created=1, updated=0, skipped=0" },
      error_message: null,
      error_code: null,
      runtime_path: "production",
    })

    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("OCP 集群租户导入")
    await userEvent.click(screen.getByRole("button", { name: "执行 OCP 集群租户导入" }))
    expect(screen.queryByText("当前执行会直接修改平台对象。")).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "执行" }))
    await waitFor(() => {
      expect(functionsApi.invoke).toHaveBeenCalledWith(7, expect.objectContaining({
        write_mode: "write",
        execution_mode: "apply",
        confirm_apply: true,
        runtime_path: "production",
      }))
    })
  })

  it("formats object-like invoke errors instead of rendering object object", async () => {
    functionsApi.list.mockResolvedValueOnce([
      { id: 8, name: "OCP 集群租户导入", slug: "fn-ocp-import", kind: "built_in", status: "released" },
    ])
    functionsApi.get.mockResolvedValueOnce({
      id: 8,
      name: "OCP 集群租户导入",
      slug: "fn-ocp-import",
      kind: "built_in",
      status: "released",
      draft_code: "def main(payload, context):\n    return {}\n",
      draft_dependencies: {
        invoke: {
          mode: "write_apply",
          requires_confirmation: true,
          result_mode: "output",
        },
      },
    })
    functionsApi.invoke.mockRejectedValueOnce({
      response: {
        data: {
          detail: {
            message: "当前测试执行仅支持预演，不能直接修改平台对象。发布后可通过正式执行或 Scheduler 生效。",
            error_code: "apply_confirmation_required",
            blocked_action: "datasource.create",
          },
        },
      },
      message: "Request failed",
    })

    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("OCP 集群租户导入")
    await userEvent.click(screen.getByRole("button", { name: "执行 OCP 集群租户导入" }))
    await userEvent.click(screen.getByRole("button", { name: "执行" }))

    expect(await screen.findByText(/"blocked_action": "datasource.create"/)).toBeInTheDocument()
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument()
  })
})
