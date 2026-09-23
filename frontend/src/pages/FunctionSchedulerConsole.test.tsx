import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"

import { renderWithShell as render } from "@/test/renderWithShell"
import { FunctionListPage } from "./FunctionListPage"
import { SchedulerConsolePage } from "./SchedulerConsolePage"

const FunctionBuildPage = () => <div>Build Chat</div> // Navigation target only; native workspace has its own tests.

const { functionsApi, schedulesApi, datasourcesApi, agentsApi } = vi.hoisted(() => ({
  functionsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    listAllRuns: vi.fn(),
    invoke: vi.fn(),
    cancelRun: vi.fn(),
    suggestInput: vi.fn(),
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

}))

vi.mock("@/lib/api", () => ({
  functionsApi,
  schedulesApi,
  agentsApi,
  datasourcesApi,
  filterConnectableDatasources: (items: Array<{ status?: string }>) =>
    items.filter((item) => item.status === "active"),
}))

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>
}

describe("Function and Scheduler consoles", () => {
  beforeEach(() => {
    vi.resetAllMocks()
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
    functionsApi.cancelRun.mockResolvedValue({
      status: "cancelled",
      duration_ms: 9,
      error_class: "cancelled",
      error_code: "cancelled",
      error_message: "Function invocation cancelled",
      output: null,
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
    functionsApi.listAllRuns.mockResolvedValue([])
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

  it("keeps scheduler console focused on lifecycle + execution drawer", async () => {
    render(
      <MemoryRouter initialEntries={["/scheduler/100"]}>
        <Routes>
          <Route path="/scheduler/:schedulerId" element={<SchedulerConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByRole("button", { name: "新建" })).toBeInTheDocument()
    expect(await screen.findByText("daily-job")).toBeInTheDocument()
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
    await userEvent.click(screen.getByRole("button", { name: "编辑 daily-job" }))
    await userEvent.type(
      screen.getByPlaceholderText("例如：改为每 10 分钟执行，失败重试 2 次，切换上海时区"),
      "改成每 5 分钟执行一次，失败重试 3 次"
    )
    await userEvent.click(screen.getByRole("button", { name: "AI 调整当前 Scheduler" }))

    expect(schedulesApi.build).toHaveBeenCalledWith(100, "改成每 5 分钟执行一次，失败重试 3 次")
  })

  it("keeps native approval waiting distinct from success and opens the original conversation", async () => {
    schedulesApi.listAllRunsPage.mockResolvedValue({
      items: [{ id: 2, schedule_id: 100, run_id: "native-occurrence", target_type: "agent",
        status: "waiting_approval", runtime_status: "waiting_approval", conversation_id: "native-conversation",
        trigger_type: "manual", attempt: 1, retry_count: 0, max_retries: 0, created_at: "2026-09-16T10:00:00Z" }],
      total: 1, limit: 20, offset: 0,
    })
    render(<MemoryRouter initialEntries={["/scheduler/100"]}>
      <LocationProbe />
      <Routes><Route path="/scheduler/:schedulerId" element={<SchedulerConsolePage />} />
        <Route path="/chat" element={<div>Native chat destination</div>} /></Routes>
    </MemoryRouter>)
    await userEvent.click(screen.getByRole("tab", { name: "执行记录" }))
    await userEvent.click(await screen.findByText("native-occurrence"))
    expect(screen.getByText("状态: 等待批准")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "修复假 running" })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "查看对话与处理审批" }))
    expect(await screen.findByText("Native chat destination")).toBeInTheDocument()
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
      { id: 7, name: "外部资产导入", slug: "fn-external-import", kind: "built_in", status: "released" },
    ])
    functionsApi.get.mockResolvedValueOnce({
      id: 7,
      name: "外部资产导入",
      slug: "fn-external-import",
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
      run_id: "invoke-external-1",
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

    await screen.findByText("外部资产导入")
    await userEvent.click(screen.getByRole("button", { name: "执行 外部资产导入" }))
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

  it("assigns a run id before invocation and can stop the owned run", async () => {
    functionsApi.list.mockResolvedValueOnce([
      { id: 12, name: "slow-function", status: "released" },
    ])
    functionsApi.get.mockResolvedValueOnce({
      id: 12,
      name: "slow-function",
      status: "released",
      draft_code: "import time\ntime.sleep(10)",
      draft_dependencies: null,
    })
    let finishInvocation: ((value: Record<string, unknown>) => void) | undefined
    functionsApi.invoke.mockImplementationOnce(
      () => new Promise((resolve) => { finishInvocation = resolve })
    )
    functionsApi.cancelRun.mockImplementationOnce(async () => {
      const result = {
        status: "cancelled",
        duration_ms: 9,
        error_class: "cancelled",
        error_code: "cancelled",
        error_message: "Function invocation cancelled",
        output: null,
      }
      finishInvocation?.(result)
      return result
    })

    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Routes>
          <Route path="/function" element={<FunctionListPage />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByText("slow-function")
    await userEvent.click(screen.getByRole("button", { name: "执行 slow-function" }))
    await userEvent.click(screen.getByRole("button", { name: "执行" }))
    const stop = await screen.findByRole("button", { name: "停止执行" })
    const invokeRequest = functionsApi.invoke.mock.calls[0][1]
    expect(invokeRequest.run_id).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/)
    await userEvent.click(stop)

    await waitFor(() => {
      expect(functionsApi.cancelRun).toHaveBeenCalledWith(12, invokeRequest.run_id)
    })
    expect(await screen.findByText(/执行已停止/)).toBeInTheDocument()
  })

  it("formats object-like invoke errors instead of rendering object object", async () => {
    functionsApi.list.mockResolvedValueOnce([
      { id: 8, name: "外部资产导入", slug: "fn-external-import", kind: "built_in", status: "released" },
    ])
    functionsApi.get.mockResolvedValueOnce({
      id: 8,
      name: "外部资产导入",
      slug: "fn-external-import",
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

    await screen.findByText("外部资产导入")
    await userEvent.click(screen.getByRole("button", { name: "执行 外部资产导入" }))
    await userEvent.click(screen.getByRole("button", { name: "执行" }))

    expect(await screen.findByText(/"blocked_action": "datasource.create"/)).toBeInTheDocument()
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument()
  })
})
