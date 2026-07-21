import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { StatsAnalysisPage } from "./StatsAnalysisPage"

const { datasourcesApi, statsAnalysisApi, toast } = vi.hoisted(() => ({
  datasourcesApi: {
    list: vi.fn(),
  },
  statsAnalysisApi: {
    getWorkbench: vi.fn(),
    getDrawerDetail: vi.fn(),
    getDailyCollectionSummary: vi.fn(),
    getDailyFailedTables: vi.fn(),
    getDailyTasks: vi.fn(),
    listRiskCandidates: vi.fn(),
    listRiskCollectionRuns: vi.fn(),
    collectRiskCandidates: vi.fn(),
    submitRiskAnalysis: vi.fn(),
    streamRiskAnalysis: vi.fn(),
    getRiskAnalysis: vi.fn(),
  },
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}))
const { conversationsApi, chatApi, messagesApi, consumeRuntimeSse } = vi.hoisted(() => ({
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
  datasourcesApi,
  statsAnalysisApi,
  conversationsApi,
  chatApi,
  messagesApi,
}))

vi.mock("sonner", () => ({
  toast,
}))

vi.mock("@/lib/runtimeStream", () => ({
  consumeRuntimeSse,
  formatRuntimeCoreMessage: (event: { summary?: string }) => event.summary || "处理中",
}))

describe("StatsAnalysisPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
      options?.onEvent?.({ kind: "assistant", text: "这是诊断结论。" })
      options?.onEvent?.({ kind: "core", name: "done", summary: "完成" })
      return { donePayload: {}, assistantText: "这是诊断结论。" }
    })
    datasourcesApi.list.mockResolvedValue([
      {
        id: 11,
        name: "monitor-a-sys",
        host: "127.0.0.1",
        port: 2881,
        db_type: "oceanbase",
        cluster_key: "cluster-a",
        tenant_role: "sys",
        user: "sys",
        database: "oceanbase",
        status: "active",
        created_at: "2026-04-04T00:00:00Z",
        updated_at: "2026-04-04T00:00:00Z",
      },
      {
        id: 13,
        name: "monitor-a-user",
        host: "127.0.0.3",
        port: 2881,
        db_type: "oceanbase",
        cluster_key: "cluster-a",
        tenant_role: "user",
        user: "monitor",
        database: "monitor_db",
        status: "active",
        created_at: "2026-04-04T00:00:00Z",
        updated_at: "2026-04-04T00:00:00Z",
      },
      {
        id: 12,
        name: "monitor-b-sys",
        host: "127.0.0.2",
        port: 2881,
        db_type: "oceanbase",
        cluster_key: "cluster-b",
        tenant_role: "sys",
        user: "sys",
        database: "oceanbase",
        status: "active",
        created_at: "2026-04-04T00:00:00Z",
        updated_at: "2026-04-04T00:00:00Z",
      },
    ])
    statsAnalysisApi.getWorkbench.mockImplementation(({ datasource_id }: { datasource_id?: number }) => {
      const dsId = datasource_id ?? 0
      return Promise.resolve({
        datasource_id: datasource_id ?? null,
        cluster_key: datasource_id === 11 ? "cluster-a" : datasource_id === 12 ? "cluster-b" : null,
        overview: {
          task_summary: {
            total_tasks: 5,
            success_tasks: 4,
            failed_tasks: 1,
            failed_task_ratio_pct: 20,
            total_tables_planned: 100,
            total_tables_failed: 1,
          },
          scheduler_windows: [],
        },
        cards: [
          { key: "scheduler", title: "调度健康", value: "异常", status: "critical", hint: "启用窗口 6/7" },
          { key: "failed_tables", title: "失败表", value: "1", status: "critical", hint: "近 7 天失败表对象" },
          { key: "stale_stats", title: "过期/缺失统计", value: "1", status: "warning", hint: "近 7 天过期/缺失对象" },
          { key: "risk_candidates", title: "风险候选", value: "1", status: "warning", hint: "失败 / 过期 / 高变化合并视图" },
        ],
        issues: [
          {
            issue_id: `failed:${dsId}`,
            kind: "failed_table",
            severity: "high",
            title: `orders_${dsId} 收集失败`,
            summary: "自动收集任务未成功完成。",
            datasource_id: dsId,
            cluster_key: dsId === 12 ? "cluster-b" : "cluster-a",
            tenant_name: "tenant_a",
            database_name: "app",
            table_name: `orders_${dsId}`,
            facts: { error_reason: "-4012 timeout", gather_seconds: 2400 },
          },
          {
            issue_id: `dml:${dsId}`,
            kind: "dml_change",
            severity: "medium",
            title: `sessions_${dsId} 数据变化显著`,
            summary: "短时间内 DML 变化比例高。",
            datasource_id: dsId,
            cluster_key: dsId === 12 ? "cluster-b" : "cluster-a",
            tenant_name: "tenant_a",
            database_name: "app",
            table_name: `sessions_${dsId}`,
            facts: { row_change_delta: 2200000 },
          },
        ],
        warnings: datasource_id === 12 ? ["当前集群缺少 scheduler run detail，将以降级模式输出。"] : [],
        tenant_config_checks: [],
      })
    })
    statsAnalysisApi.getDrawerDetail.mockImplementation(
      ({
        datasource_id,
        issue,
        risk_candidate,
      }: {
        datasource_id: number
        issue?: { title: string; summary: string; kind: string; severity: "high" | "medium" | "low"; table_name?: string | null; database_name?: string | null }
        risk_candidate?: { database_name: string; table_name: string; severity: "high" | "medium" | "low"; latest_summary?: string | null; tags: Array<{ tag_label: string }> }
      }) => {
        if (risk_candidate) {
          return Promise.resolve({
            datasource_id,
            title: `${risk_candidate.database_name}.${risk_candidate.table_name} 风险分析`,
            object_kind: "dml_change",
            severity: risk_candidate.severity,
            summary: risk_candidate.latest_summary || "候选池深度分析任务",
            subtitle: `cluster-a / tenant_a / ${risk_candidate.database_name} / ${risk_candidate.table_name}`,
            sections: [
              {
                key: "scope",
                title: "对象范围",
                fields: [
                  { label: "库", value: risk_candidate.database_name },
                  { label: "表", value: risk_candidate.table_name },
                ],
              },
              {
                key: "risk-tags",
                title: "风险标签",
                fields: [{ label: "标签", value: risk_candidate.tags.map((tag) => tag.tag_label).join(" / ") }],
              },
            ],
            history_rows: [],
            history_source: null,
            missing_facts: [],
            chat_context: {
              database_name: risk_candidate.database_name,
              table_name: risk_candidate.table_name,
              tenant_name: "tenant_a",
              tags: risk_candidate.tags.map((tag) => tag.tag_label),
            },
          })
        }
        return Promise.resolve({
          datasource_id,
          title: issue?.title || "统计分析详情",
          object_kind: issue?.kind || "failed_table",
          severity: issue?.severity || "high",
          summary: issue?.summary || "自动收集任务未成功完成。",
          subtitle: `cluster-a / tenant_a / ${issue?.database_name || "app"} / ${issue?.table_name || "orders"}`,
          sections: [
            {
              key: "scope",
              title: "对象范围",
              fields: [
                { label: "数据源", value: datasource_id === 11 ? "monitor-a-sys" : `datasource-${datasource_id}` },
                { label: "库", value: issue?.database_name || "app" },
                { label: "表", value: issue?.table_name || "orders" },
              ],
            },
            {
              key: "task",
              title: "任务信息",
              fields: [
                { label: "触发方式", value: "自动收集" },
                { label: "开始时间", value: "2026-04-04 00:00:00" },
                { label: "错误码", value: "-4012" },
              ],
            },
          ],
          history_rows: [
            {
              task_id: "task-1",
              start_time: "2026-04-04 00:00:00",
              status: "FAILED",
              ret_code: "-4012",
              gather_seconds: 2400,
              trigger_type: "自动收集",
            },
          ],
          history_source: "table_gather_history_sys",
          missing_facts: [],
          chat_context: {
            database_name: issue?.database_name || "app",
            table_name: issue?.table_name || "orders",
            tenant_name: "tenant_a",
            error_code: "-4012",
            error_reason: "-4012 timeout",
          },
        })
      }
    )
    statsAnalysisApi.getDailyCollectionSummary.mockImplementation(({ datasource_id }: { datasource_id?: number }) =>
      Promise.resolve({ datasource_id: datasource_id ?? null, items: [] })
    )
    statsAnalysisApi.getDailyFailedTables.mockImplementation(({ datasource_id }: { datasource_id?: number }) =>
      Promise.resolve({ datasource_id: datasource_id ?? null, date: "2026-04-04", items: [] })
    )
    statsAnalysisApi.getDailyTasks.mockImplementation(({ datasource_id }: { datasource_id?: number }) =>
      Promise.resolve({ datasource_id: datasource_id ?? null, date: "2026-04-04", items: [], total: 0, page: 1, page_size: 10 })
    )
    statsAnalysisApi.listRiskCandidates.mockImplementation(({ datasource_id }: { datasource_id: number }) =>
      Promise.resolve({
        datasource_id,
        items: [
          {
            candidate_id: datasource_id * 100 + 1,
            datasource_id,
            cluster_key: datasource_id === 11 ? "cluster-a" : "cluster-b",
            tenant_name: "tenant_a",
            database_name: "app",
            table_name: `sessions_${datasource_id}`,
            severity: "medium",
            score: 72,
            lifecycle_status: "active",
            source: "dml_change",
            latest_summary: "短时间内 DML 变化比例高。",
            last_seen_at: "2026-04-04T00:00:00Z",
            tags: [
              {
                tag_key: "high_dml_change",
                tag_label: "数据变化显著",
                severity: "medium",
                score: 72,
                facts: { row_change_delta: 2200000 },
              },
            ],
          },
        ],
      })
    )
    statsAnalysisApi.listRiskCollectionRuns.mockResolvedValue({
      datasource_id: 11,
      items: [
        {
          run_id: "risk-collect-1",
          datasource_id: 11,
          trigger_type: "auto",
          status: "ready",
          started_at: "2026-04-04T00:00:00Z",
          finished_at: "2026-04-04T00:01:00Z",
        },
      ],
    })
    statsAnalysisApi.collectRiskCandidates.mockResolvedValue({
      datasource_id: 11,
      collected_tables: 1,
      active_candidates: 1,
      expired_candidates: 0,
    })
    statsAnalysisApi.submitRiskAnalysis.mockResolvedValue({
      run_id: "risk-1",
      status: "pending",
    })
    statsAnalysisApi.streamRiskAnalysis.mockImplementation(async (_params: unknown, handlers?: { onEvent?: (event: any) => void }) => {
      handlers?.onEvent?.({ type: "phase", data: { phase: "submitted", run_id: "risk-1", status: "running" } })
      handlers?.onEvent?.({ type: "delta", data: { run_id: "risk-1", chunk: "正在分析高 DML 变化与收集策略。\n" } })
      handlers?.onEvent?.({
        type: "done",
        data: {
          run_id: "risk-1",
          status: "ready",
          result: {
            headline: "建议补充直方图评估",
            verdict: "stale_or_missing_stats",
            reasoning: "候选标签显示数据变化显著。",
            evidence: [],
            next_actions: [],
            missing_facts: [],
            diagnosis_path: [],
            risks: [],
          },
        },
      })
    })
    statsAnalysisApi.getRiskAnalysis.mockResolvedValue({
      run_id: "risk-1",
      status: "ready",
      result: {
        headline: "建议补充直方图评估",
        verdict: "stale_or_missing_stats",
        reasoning: "候选标签显示数据变化显著。",
        evidence: [],
        next_actions: [],
        missing_facts: [],
        diagnosis_path: [],
        risks: [],
      },
    })
  })

  it("renders workbench and collection summary", async () => {
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()
    expect(screen.queryByText("工作台概况")).not.toBeInTheDocument()
    expect(await screen.findByText("风险候选")).toBeInTheDocument()
    expect(await screen.findByText("近 7 天没有收集任务记录。")).toBeInTheDocument()
  })

  it("switches cluster and updates workbench with cluster_key", async () => {
    const user = userEvent.setup()
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: "集群范围" }))
    await user.click(await screen.findByRole("option", { name: "cluster-b" }))

    await waitFor(() => {
      expect(statsAnalysisApi.getWorkbench).toHaveBeenCalledWith(
        expect.objectContaining({ cluster_key: "cluster-b" })
      )
    })
  })

  it("keeps cluster scope on day overview detail requests when datasource scope is all", async () => {
    const user = userEvent.setup()
    statsAnalysisApi.getDailyCollectionSummary.mockImplementation(({ datasource_id, cluster_key }: { datasource_id?: number | null; cluster_key?: string | null }) =>
      Promise.resolve({
        datasource_id: datasource_id ?? null,
        items: [
          {
            date: "2026-04-04",
            task_type: "AUTO GATHER",
            total_tasks: 3,
            success_tasks: 2,
            failed_tasks: 1,
            total_tables: 20,
            success_tables: 19,
            failed_tables: 1,
            avg_duration_min: 8,
            max_duration_min: 12,
            cluster_key: cluster_key ?? "cluster-b",
            tenant_name: "tenant_b",
            datasource_id: 12,
          },
        ],
      })
    )

    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()
    await user.click(screen.getByRole("combobox", { name: "集群范围" }))
    await user.click(await screen.findByRole("option", { name: "cluster-b" }))

    await waitFor(() => {
      expect(statsAnalysisApi.getDailyCollectionSummary).toHaveBeenCalledWith(
        expect.objectContaining({ cluster_key: "cluster-b" })
      )
    })

    const summaryRows = document.querySelectorAll("tbody tr")
    expect(summaryRows.length).toBeGreaterThan(0)
    await user.click(summaryRows[0] as HTMLElement)

    await waitFor(() => {
      expect(statsAnalysisApi.getDailyFailedTables).toHaveBeenCalledWith(
        expect.objectContaining({ cluster_key: "cluster-b", date: "2026-04-04" })
      )
      expect(statsAnalysisApi.getDailyTasks).toHaveBeenCalledWith(
        expect.objectContaining({ cluster_key: "cluster-b", date: "2026-04-04" })
      )
    })
  })

  it("supports datasource-level filter within the same cluster", async () => {
    const user = userEvent.setup()
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: "数据源范围" }))
    await user.click(await screen.findByRole("option", { name: "monitor-a-user" }))

    await waitFor(() => {
      expect(statsAnalysisApi.getWorkbench).toHaveBeenCalledWith(
        expect.objectContaining({ datasource_id: 13 })
      )
    })
  })

  it("switches to risk tab and shows risk candidates after selecting datasource", async () => {
    const user = userEvent.setup()
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()

    // Select a specific datasource (risk candidates require datasource_id)
    await user.click(screen.getByRole("combobox", { name: "数据源范围" }))
    await user.click(await screen.findByRole("option", { name: "monitor-a-sys" }))

    await user.click(screen.getByRole("tab", { name: "风险检测" }))

    expect(await screen.findByText("app.sessions_11")).toBeInTheDocument()
  })

  it("opens risk drawer when clicking table row", async () => {
    const user = userEvent.setup()
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: "数据源范围" }))
    await user.click(await screen.findByRole("option", { name: "monitor-a-sys" }))

    await user.click(screen.getByRole("tab", { name: "风险检测" }))

    const rowCell = await screen.findByText("app.sessions_11")
    const row = rowCell.closest("tr")
    expect(row).not.toBeNull()
    await user.click(row!)
    expect(await screen.findByText("风险标签")).toBeInTheDocument()
  })

  it("navigates to risk tab when no failed table exists", async () => {
    const user = userEvent.setup()
    render(<StatsAnalysisPage />)

    // Select a datasource first so risk candidates load
    expect(await screen.findByText("调度健康")).toBeInTheDocument()
    await user.click(screen.getByRole("combobox", { name: "数据源范围" }))
    await user.click(await screen.findByRole("option", { name: "monitor-a-sys" }))

    await waitFor(() => {
      expect(screen.getByText("近 7 天没有收集任务记录。")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("button", { name: "查看风险检测" }))
    expect(await screen.findByText("app.sessions_11")).toBeInTheDocument()
  })

  it("keeps risk tab available while workbench is still loading", async () => {
    const user = userEvent.setup()
    // First call (all-mode) resolves immediately; second call (after ds select) hangs
    let resolveWorkbench: ((value: unknown) => void) | null = null
    let callCount = 0
    statsAnalysisApi.getWorkbench.mockImplementation(({ datasource_id }: { datasource_id?: number }) => {
      callCount += 1
      if (callCount <= 1) {
        // First call (all-mode) resolves normally
        return Promise.resolve({
          datasource_id: datasource_id ?? null,
          cluster_key: null,
          overview: { task_summary: { total_tasks: 0, success_tasks: 0, failed_tasks: 0, failed_task_ratio_pct: 0, total_tables_planned: 0, total_tables_failed: 0 }, scheduler_windows: [] },
          cards: [],
          issues: [],
          warnings: [],
          tenant_config_checks: [],
        })
      }
      // Second call (datasource-specific) hangs
      return new Promise((resolve) => { resolveWorkbench = resolve })
    })

    render(<StatsAnalysisPage />)

    // Wait for initial all-mode load, then select a datasource to trigger risk candidates
    expect(await screen.findByText("近 7 天没有收集任务记录。")).toBeInTheDocument()
    await user.click(screen.getByRole("combobox", { name: "数据源范围" }))
    await user.click(await screen.findByRole("option", { name: "monitor-a-sys" }))

    // Risk tab should be available even while workbench is loading
    expect(await screen.findByRole("tab", { name: "风险检测" })).toBeInTheDocument()
    await user.click(screen.getByRole("tab", { name: "风险检测" }))
    expect(await screen.findByText("app.sessions_11")).toBeInTheDocument()

    resolveWorkbench?.({
      datasource_id: 11,
      cluster_key: "cluster-a",
      overview: { task_summary: { total_tasks: 5, success_tasks: 4, failed_tasks: 1, failed_task_ratio_pct: 20, total_tables_planned: 100, total_tables_failed: 1 }, scheduler_windows: [] },
      cards: [],
      issues: [{ issue_id: "failed:11", kind: "failed_table", severity: "high", title: "orders_11 收集失败", summary: "自动收集任务未成功完成。", tenant_name: "tenant_a", database_name: "app", table_name: "orders_11", facts: { error_reason: "-4012 timeout", gather_seconds: 2400 } }],
      warnings: [],
      tenant_config_checks: [],
    })

    expect(await screen.findByText("app.sessions_11")).toBeInTheDocument()
  })

  it("opens AI 分析 tab from failed table in all mode", async () => {
    const user = userEvent.setup()
    statsAnalysisApi.getDailyCollectionSummary.mockResolvedValueOnce({
      datasource_id: null,
      items: [
        {
          date: "2026-04-04",
          task_type: "AUTO GATHER",
          total_tasks: 5,
          success_tasks: 4,
          failed_tasks: 1,
          total_tables: 100,
          success_tables: 99,
          failed_tables: 1,
          avg_duration_min: 12,
          max_duration_min: 18,
          cluster_key: "cluster-a",
          tenant_name: "tenant_a",
          datasource_id: 13,
        },
      ],
    })
    statsAnalysisApi.getDailyFailedTables.mockResolvedValueOnce({
      datasource_id: null,
      date: "2026-04-04",
      items: [
        {
          owner: "wx",
          table_name: "tb_transactions",
          failure_count: 1,
          latest_status: "FAILED",
          latest_error: "-4012 timeout",
          latest_gather_seconds: 2400,
          latest_task_start_time: "2026-04-04 00:00:00",
          cluster_key: "cluster-a",
          tenant_name: "wx",
          datasource_id: 13,
        },
      ],
    })

    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()
    const summaryRows = document.querySelectorAll("tbody tr")
    expect(summaryRows.length).toBeGreaterThan(0)
    await user.click(summaryRows[0] as HTMLElement)
    await user.click(await screen.findByText("wx.tb_transactions"))
    await user.click(await screen.findByRole("tab", { name: "AI 分析" }))

    expect(await screen.findByPlaceholderText("输入诊断问题，例如：为什么这张表需要直方图？")).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "AI 分析" })).toHaveAttribute("data-state", "active")
  })

  it("passes drawer datasource in scene agent payload when manually chatting from failed table in all mode", async () => {
    const user = userEvent.setup()
    statsAnalysisApi.getDailyCollectionSummary.mockResolvedValueOnce({
      datasource_id: null,
      items: [
        {
          date: "2026-04-04",
          task_type: "AUTO GATHER",
          total_tasks: 5,
          success_tasks: 4,
          failed_tasks: 1,
          total_tables: 100,
          success_tables: 99,
          failed_tables: 1,
          avg_duration_min: 12,
          max_duration_min: 18,
          cluster_key: "cluster-a",
          tenant_name: "tenant_a",
          datasource_id: 13,
        },
      ],
    })
    statsAnalysisApi.getDailyFailedTables.mockResolvedValueOnce({
      datasource_id: null,
      date: "2026-04-04",
      items: [
        {
          owner: "wx",
          table_name: "tb_transactions",
          failure_count: 1,
          latest_status: "FAILED",
          latest_error: "-4012 timeout",
          latest_gather_seconds: 2400,
          latest_task_start_time: "2026-04-04 00:00:00",
          cluster_key: "cluster-a",
          tenant_name: "wx",
          datasource_id: 13,
        },
      ],
    })

    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()
    const summaryRows = document.querySelectorAll("tbody tr")
    expect(summaryRows.length).toBeGreaterThan(0)
    await user.click(summaryRows[0] as HTMLElement)
    await user.click(await screen.findByText("wx.tb_transactions"))
    await user.click(await screen.findByRole("tab", { name: "AI 分析" }))
    await user.type(await screen.findByPlaceholderText("输入诊断问题，例如：为什么这张表需要直方图？"), "这张表是否需要直方图？依据是什么？{enter}")

    await waitFor(() => {
      expect(chatApi.stream).toHaveBeenCalled()
    })
    expect(chatApi.stream).toHaveBeenCalledWith(
      101,
      "这张表是否需要直方图？依据是什么？",
      expect.objectContaining({
        runDatasourceIds: [13],
        sceneAgent: expect.objectContaining({
          focus_object: expect.objectContaining({
            database_name: null,
            tenant_name: "wx",
            table_name: "tb_transactions",
          }),
          context: expect.objectContaining({
            datasource: expect.objectContaining({ id: 13, name: "monitor-a-user", tenant_role: "user" }),
          }),
        }),
      })
    )
  })

  it("passes day summary datasource in scene agent payload when chatting from all-mode overview drawer", async () => {
    const user = userEvent.setup()
    statsAnalysisApi.getDailyCollectionSummary.mockResolvedValueOnce({
      datasource_id: null,
      items: [
        {
          date: "2026-04-04",
          task_type: "AUTO GATHER",
          total_tasks: 5,
          success_tasks: 4,
          failed_tasks: 1,
          total_tables: 100,
          success_tables: 99,
          failed_tables: 1,
          avg_duration_min: 12,
          max_duration_min: 18,
          cluster_key: "cluster-a",
          tenant_name: "tenant_a",
          datasource_id: 13,
        },
      ],
    })
    statsAnalysisApi.getDailyFailedTables.mockResolvedValueOnce({
      datasource_id: null,
      date: "2026-04-04",
      items: [
        {
          owner: "wx",
          table_name: "tb_transactions",
          failure_count: 1,
          latest_status: "FAILED",
          latest_error: "-4012 timeout",
          latest_gather_seconds: 2400,
          latest_task_start_time: "2026-04-04 00:00:00",
          cluster_key: "cluster-a",
          tenant_name: "wx",
          datasource_id: 13,
        },
      ],
    })

    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()
    const summaryRows = document.querySelectorAll("tbody tr")
    expect(summaryRows.length).toBeGreaterThan(0)
    await user.click(summaryRows[0] as HTMLElement)
    await user.click(await screen.findByRole("tab", { name: "AI 分析" }))
    await user.type(await screen.findByPlaceholderText("输入诊断问题，例如：为什么这张表需要直方图？"), "这张表是否需要直方图？依据是什么？{enter}")

    await waitFor(() => {
      expect(chatApi.stream).toHaveBeenCalled()
    })
    expect(chatApi.stream).toHaveBeenCalledWith(
      101,
      "这张表是否需要直方图？依据是什么？",
      expect.objectContaining({
        runDatasourceIds: [13],
        sceneAgent: expect.objectContaining({
          focus_object: expect.objectContaining({
            datasource_id: 13,
          }),
          context: expect.objectContaining({
            datasource: expect.objectContaining({ id: 13, name: "monitor-a-user", tenant_role: "user" }),
          }),
        }),
      })
    )
  })

  it("keeps tool cards in stats drawer chat after stream completion", async () => {
    const user = userEvent.setup()
    chatApi.listEvents.mockResolvedValueOnce([
      {
        id: 401,
        conversation_id: 101,
        event_type: "user_message",
        turn_seq: 1,
        part_seq: 0,
        role: "user",
        created_at: "2026-03-14T00:00:01Z",
        payload: { content: "继续分析", message_id: 1 },
      },
      {
        id: 402,
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
        id: 403,
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

    render(<StatsAnalysisPage />)

    expect(await screen.findByText("调度健康")).toBeInTheDocument()
    await user.click(screen.getByRole("combobox", { name: "数据源范围" }))
    await user.click(await screen.findByRole("option", { name: "monitor-a-sys" }))
    await user.click(screen.getByRole("tab", { name: "风险检测" }))

    const rowCell = await screen.findByText("app.sessions_11")
    const row = rowCell.closest("tr")
    expect(row).not.toBeNull()
    await user.click(row!)
    await user.click(await screen.findByRole("tab", { name: "AI 分析" }))
    await user.type(await screen.findByPlaceholderText("输入诊断问题，例如：为什么这张表需要直方图？"), "继续分析{enter}")

    expect(await screen.findByText("这是诊断结论。")).toBeInTheDocument()
    expect(await screen.findByText(/工具调用：execute_sql/)).toBeInTheDocument()
    await waitFor(() => expect(chatApi.listEvents).toHaveBeenCalledTimes(1))
  })


  it("shows collection tab empty state with redirect to risk", async () => {
    const user = userEvent.setup()
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("近 7 天没有收集任务记录。")).toBeInTheDocument()

    // Select datasource to enable risk candidates
    await user.click(screen.getByRole("combobox", { name: "数据源范围" }))
    await user.click(await screen.findByRole("option", { name: "monitor-a-sys" }))

    await waitFor(() => {
      expect(screen.getByText("近 7 天没有收集任务记录。")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("button", { name: "查看风险检测" }))
    expect(await screen.findByText("app.sessions_11")).toBeInTheDocument()
  })

  it("opens tenant config drawer from scheduler card when checks are present", async () => {
    const user = userEvent.setup()
    statsAnalysisApi.getWorkbench.mockResolvedValueOnce({
      datasource_id: 11,
      cluster_key: "cluster-a",
      overview: {
        task_summary: { total_tasks: 0, success_tasks: 0, failed_tasks: 0, failed_task_ratio_pct: 0, total_tables_planned: 0, total_tables_failed: 0 },
        scheduler_windows: [],
      },
      cards: [
        { key: "scheduler", title: "调度健康", value: "待确认", status: "warning", hint: "启用窗口 0/7" },
      ],
      issues: [],
      warnings: [],
      tenant_config_checks: [
        {
          tenant_name: "biz_tenant",
          datasource_id: 13,
          auto_gather_enabled: false,
          enabled_windows: 5,
          total_windows: 7,
          recent_task_count: 0,
          issue_type: "auto_gather_disabled",
          issue_label: "自动采集未启用",
          suggestion_sql: "ALTER SYSTEM SET _enable_auto_stat_gather = true;",
        },
      ],
    })
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("1 个租户需关注，点击查看详情")).toBeInTheDocument()
    expect(screen.queryByText("租户配置检查")).not.toBeInTheDocument()
    await user.click(screen.getByText("调度健康"))
    expect(await screen.findByText("biz_tenant — 配置优化")).toBeInTheDocument()
    expect(screen.getByText("自动采集未启用")).toBeInTheDocument()
  })

  it("does not show all-healthy copy when tenant windows are only partially enabled", async () => {
    statsAnalysisApi.getWorkbench.mockResolvedValueOnce({
      datasource_id: 11,
      cluster_key: "cluster-a",
      overview: {
        task_summary: { total_tasks: 0, success_tasks: 0, failed_tasks: 0, failed_task_ratio_pct: 0, total_tables_planned: 0, total_tables_failed: 0 },
        scheduler_windows: [],
      },
      cards: [
        { key: "scheduler", title: "调度健康", value: "异常", status: "critical", hint: "启用窗口 11/14" },
      ],
      issues: [],
      warnings: [],
      tenant_config_checks: [
        {
          tenant_name: "biz_tenant",
          datasource_id: 13,
          auto_gather_enabled: true,
          enabled_windows: 6,
          total_windows: 7,
          recent_task_count: 2,
          issue_type: "partial_windows",
          issue_label: "调度窗口部分启用（6/7）",
          suggestion_sql: "CALL DBMS_SCHEDULER.ENABLE('MONDAY_WINDOW');",
        },
      ],
    })
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("1 个租户需关注，点击查看详情")).toBeInTheDocument()
    expect(screen.queryByText("租户配置检查")).not.toBeInTheDocument()
    expect(screen.queryByText("当前未发现租户级自动采集配置异常")).not.toBeInTheDocument()
    expect(screen.queryByText("所有租户的自动采集配置均已正确启用")).not.toBeInTheDocument()
  })

  it("shows downgraded healthy copy when all tenant config checks are healthy", async () => {
    statsAnalysisApi.getWorkbench.mockResolvedValueOnce({
      datasource_id: 11,
      cluster_key: "cluster-a",
      overview: {
        task_summary: { total_tasks: 2, success_tasks: 2, failed_tasks: 0, failed_task_ratio_pct: 0, total_tables_planned: 10, total_tables_failed: 0 },
        scheduler_windows: [],
      },
      cards: [
        { key: "scheduler", title: "调度健康", value: "正常", status: "healthy", hint: "启用窗口 14/14" },
      ],
      issues: [],
      warnings: [],
      tenant_config_checks: [
        {
          tenant_name: "biz_tenant",
          datasource_id: 13,
          auto_gather_enabled: true,
          enabled_windows: 7,
          total_windows: 7,
          recent_task_count: 2,
          issue_type: "healthy",
          issue_label: "配置正常",
          suggestion_sql: "",
        },
      ],
    })
    render(<StatsAnalysisPage />)

    expect(await screen.findByText("1 个租户配置正常，点击查看详情")).toBeInTheDocument()
    expect(screen.queryByText("租户配置检查")).not.toBeInTheDocument()
    expect(screen.queryByText("所有租户的自动采集配置均已正确启用")).not.toBeInTheDocument()
  })
})
