import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes } from "react-router-dom"

import { SqlAnalysisPage } from "./SqlAnalysisPage"

const { mockSceneAgentChatShell } = vi.hoisted(() => ({
  mockSceneAgentChatShell: vi.fn(),
}))

vi.mock("@/components/shared/PageAgentChatShell", () => ({
  SceneAgentChatShell: (props: {
    embeddedInDrawer?: boolean
    adapter: { sceneKey?: string; placeholder?: string; suggestions?: string[] }
    freshSessionKey?: string | null
    suggestedPrompt?: string | null
    submitSuggestedPrompt?: boolean
    datasourceId?: number | null
    focusObject?: Record<string, unknown> | null
  }) => {
    mockSceneAgentChatShell(props)
    return (
      <div data-testid="conversation-panel" data-embedded={props.embeddedInDrawer}>
        {props.adapter.sceneKey}
      </div>
    )
  },
}))

const { datasourcesApi, sqlAnalysisApi, toast } = vi.hoisted(() => ({
  datasourcesApi: {
    list: vi.fn(),
  },
  sqlAnalysisApi: {
    listLiveCategory: vi.fn(),
    listLiveDbNames: vi.fn(),
    buildLiveContext: vi.fn(),
    getLivePlanExplain: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  datasourcesApi,
  sqlAnalysisApi,
}))

vi.mock("sonner", () => ({
  toast,
}))

const MOCK_DATASOURCE = {
  id: 5,
  name: "monitor-a",
  host: "127.0.0.1",
  port: 2881,
  db_type: "oceanbase",
  cluster_key: "cluster-a",
  tenant_role: "user",
  user: "monitor",
  database: "monitor_db",
  status: "active",
  created_at: "2026-03-28T00:00:00Z",
  updated_at: "2026-03-28T00:00:00Z",
}

const MOCK_WX_DATASOURCE = {
  id: 7,
  name: "monitor-wx",
  host: "127.0.0.2",
  port: 2881,
  db_type: "oceanbase",
  cluster_key: "wx",
  tenant_role: "user",
  user: "monitor",
  database: "monitor_db",
  status: "active",
  created_at: "2026-03-28T00:00:00Z",
  updated_at: "2026-03-28T00:00:00Z",
}

const MOCK_ORACLE_DATASOURCE = {
  id: 8,
  name: "wx/wxoracle",
  host: "127.0.0.3",
  port: 2881,
  db_type: "oceanbase",
  cluster_key: "wx",
  tenant_role: "user",
  tenant_identifier: "wxoracle",
  attributes: { tenant_mode: "ORACLE" },
  user: "SYS",
  database: "test",
  status: "active",
  created_at: "2026-03-28T00:00:00Z",
  updated_at: "2026-03-28T00:00:00Z",
}

const MOCK_SQL_ITEM = {
  datasource_id: 5,
  ob_tenant_id: 1002,
  tenant_name: "tenant_a",
  ob_db_id: 1,
  sql_id: "sql-2",
  db_name: "app_db",
  sql_text: "select * from biz_table",
  executions: 120,
  avg_elapsed_time_us: 5000,
  max_elapsed_time_us: 50000,
  plan_count: 2,
}

const MOCK_WX_SQL_ITEM = {
  datasource_id: 7,
  ob_tenant_id: 1003,
  tenant_name: "tenant_wx",
  ob_db_id: 2,
  sql_id: "sql-wx",
  db_name: "wx_db",
  sql_text: "select * from wx_table",
  executions: 42,
  avg_elapsed_time_us: 3000,
  max_elapsed_time_us: 12000,
  plan_count: 1,
}

const MOCK_CONTEXT = {
  datasource_id: 5,
  sql_id: "sql-2",
  start_time_us: 1,
  end_time_us: 2,
  signals: [
    {
      key: "table_scan_risk",
      severity: "warning",
      summary: "当前计划包含表扫描路径，可能导致读取放大。",
      evidence: "biz_table",
    },
  ],
  facts: {
    datasource_id: 5,
    sql_id: "sql-2",
    start_time_us: 1,
    end_time_us: 2,
    cluster_key: "cluster-a",
    tenant_id: 1002,
    db_name: "app_db",
    user_name: "root",
    sql_text: "select * from biz_table",
    latest_request_time_us: 2,
    current_plan: {
      plan_id: 77,
      plan_hash: 99,
      last_active_time: "2026-03-28 09:00:00.000000",
      table_scan: 1,
      explain_source: "explain_sql",
      explain_item_count: 1,
    },
    current_plans: [
      {
        tenant_id: 1002,
        sql_id: "sql-2",
        plan_id: 77,
        plan_hash: 99,
        executions: 8,
        avg_exe_usec: 1000,
        elapsed_time: 10000,
        execute_time: 6000,
        table_scan: 1,
        last_active_time: "2026-03-28 09:00:00.000000",
      },
    ],
    window_plan_total: 2,
    current_plan_id: 77,
    objects: ["biz_table"],
    unavailable_dimensions: [],
  },
  current_plans: [
    {
      tenant_id: 1002,
      sql_id: "sql-2",
      plan_id: 77,
      plan_hash: 99,
      executions: 8,
      avg_exe_usec: 1000,
      elapsed_time: 10000,
      execute_time: 6000,
      table_scan: 1,
      last_active_time: "2026-03-28 09:00:00.000000",
    },
    {
      tenant_id: 1002,
      sql_id: "sql-2",
      plan_id: 88,
      plan_hash: 199,
      executions: 4,
      avg_exe_usec: 2000,
      elapsed_time: 18000,
      execute_time: 12000,
      table_scan: 0,
      last_active_time: "2026-03-28 08:00:00.000000",
    },
  ],
  window_plan_total: 2,
  current_plan_id: 77,
  plan_explain: {
    datasource_id: 5,
    sql_id: "sql-2",
    plan_id: 77,
    source: "explain_sql",
    items: [
      {
        operator: "SIMPLE",
        object_name: "biz_table",
        cost: 1,
        cardinality: 10,
      },
    ],
  },
  plan_details: [],
}

describe("SqlAnalysisPage", () => {
  function renderPage(initialEntry = "/sql-analysis") {
    return render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/sql-analysis" element={<SqlAnalysisPage />} />
        </Routes>
      </MemoryRouter>
    )
  }

  beforeAll(() => {
    class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", ResizeObserverMock)
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mockSceneAgentChatShell.mockClear()
    datasourcesApi.list.mockResolvedValue([MOCK_DATASOURCE, MOCK_WX_DATASOURCE, MOCK_ORACLE_DATASOURCE])
    sqlAnalysisApi.listLiveDbNames.mockResolvedValue({ items: ["app_db", "sys_db"] })
    sqlAnalysisApi.listLiveCategory.mockResolvedValue({
      category: "top_sql",
      datasource_id: 5,
      start_time_us: 1,
      end_time_us: 2,
      limit: 50,
      items: [MOCK_SQL_ITEM],
    })
    sqlAnalysisApi.buildLiveContext.mockResolvedValue(MOCK_CONTEXT)
    sqlAnalysisApi.getLivePlanExplain.mockImplementation(async (params: { plan_id?: number }) => {
      if (params.plan_id === 88) {
        return {
          datasource_id: 5,
          sql_id: "sql-2",
          plan_id: 88,
          source: "plan_cache_explain",
          items: [
            {
              operator: "INDEX RANGE SCAN",
              object_name: "biz_indexed_table",
              cost: 2,
              cardinality: 3,
            },
          ],
        }
      }
      return {
        datasource_id: 5,
        sql_id: "sql-2",
        plan_id: 77,
        source: "explain_sql",
        items: [
          {
            operator: "SIMPLE",
            object_name: "biz_table",
            cost: 1,
            cardinality: 10,
          },
        ],
      }
    })
  })

  it("loads category list with tabs and renders table", async () => {
    renderPage()

    // Category tabs should be visible
    expect(await screen.findByRole("tab", { name: "Top SQL" })).toHaveAttribute("data-state", "active")
    expect(screen.getByRole("tab", { name: "Slow SQL" })).toBeInTheDocument()

    // Table should show SQL items
    expect(await screen.findByText("select * from biz_table")).toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Database" })).toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Executions" })).toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Avg Duration" })).toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Max Duration" })).toBeInTheDocument()
    const sqlIdCell = screen.getByText("sql-2")
    const sqlRow = sqlIdCell.closest("tr")
    expect(sqlRow).not.toBeNull()
    expect(within(sqlRow as HTMLElement).getByText("120")).toBeInTheDocument()
    // DB selector shows default "All Databases"
    expect(screen.getByText("All Databases")).toBeInTheDocument()

    // Click SQL row to open drawer
    const user = userEvent.setup()
    await user.click(screen.getByText("sql-2"))

    expect(await screen.findAllByText("select * from biz_table")).toHaveLength(2)
    expect(screen.getByText("SQL Overview")).toBeInTheDocument()
    expect(screen.getByText(/Execution Plans/)).toBeInTheDocument()
    expect(screen.getByText("Current Plan")).toBeInTheDocument()
    expect(screen.getByText("Explain Details")).toBeInTheDocument()
    expect(screen.getByText("Explain Details")).toBeInTheDocument()

    // Drawer header should have detail/AI analysis tabs
    expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute("data-state", "active")
    expect(screen.getByRole("tab", { name: "AI Analysis" })).toBeInTheDocument()

    await waitFor(() => {
      expect(datasourcesApi.list).toHaveBeenCalled()
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenCalledWith(
        expect.objectContaining({ datasource_id: 5, category: "top_sql", limit: 50 })
      )
      expect(sqlAnalysisApi.buildLiveContext).toHaveBeenCalledWith(
        expect.objectContaining({ datasource_id: 5, sql_id: "sql-2" })
      )
    })
  })

  it("switches category via tabs", async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText("select * from biz_table")
    await user.click(screen.getByRole("tab", { name: "Slow SQL" }))

    await waitFor(() => {
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenCalledWith(
        expect.objectContaining({ category: "slow_sql", limit: 50 })
      )
    })
  })

  it("switches logical plan view when selecting another plan", async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText("sql-2")
    await user.click(screen.getByText("sql-2"))
    await user.click(screen.getByText("88"))

    expect(await screen.findByText("INDEX RANGE SCAN")).toBeInTheDocument()
    expect(screen.getAllByText("biz_indexed_table").length).toBeGreaterThan(0)
  })

  it("shows time picker and defaults to first datasource in toolbar", async () => {
    renderPage()

    await screen.findByText("sql-2")
    expect(screen.getByRole("button", { name: /Last 1 hour/ })).toBeInTheDocument()
    expect(screen.getByText("All Databases")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Datasource filter" })).toHaveTextContent("monitor-a")
  })

  it("refreshes list when refresh button clicked", async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText("sql-2")
    await user.click(screen.getByRole("button", { name: /Refresh/ }))

    await waitFor(() => {
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenCalledTimes(2)
    })
  })

  it("keeps refresh enabled when current list is empty", async () => {
    const user = userEvent.setup()
    sqlAnalysisApi.listLiveCategory.mockResolvedValue({
      category: "top_sql",
      datasource_id: 5,
      start_time_us: 1,
      end_time_us: 2,
      limit: 50,
      items: [],
    })
    renderPage()

    await waitFor(() => {
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenCalledTimes(1)
    })
    expect(screen.getByText("No analyzable SQL in current window")).toBeInTheDocument()
    const refreshButton = screen.getByRole("button", { name: /Refresh/ })
    expect(refreshButton).toBeEnabled()

    await user.click(refreshButton)

    await waitFor(() => {
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenCalledTimes(2)
    })
  })

  it("filters by db name when db selector changes", async () => {
    const user = userEvent.setup()
    renderPage()

    await waitFor(() => {
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenCalledTimes(1)
    })
    await user.click(screen.getByRole("combobox", { name: "Database filter" }))
    await user.click(await screen.findByRole("option", { name: "app_db" }))

    await waitFor(() => {
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenLastCalledWith(
        expect.objectContaining({ datasource_id: 5, db_name: "app_db", limit: 50 })
      )
    })
  })

  it("selects first datasource when cluster changes", async () => {
    const user = userEvent.setup()
    sqlAnalysisApi.listLiveCategory.mockImplementation(async (params: { datasource_id: number }) => {
      if (params.datasource_id === 7) {
        return {
          category: "top_sql",
          datasource_id: 7,
          start_time_us: 1,
          end_time_us: 2,
          limit: 50,
          items: [MOCK_WX_SQL_ITEM],
        }
      }
      return {
        category: "top_sql",
        datasource_id: 5,
        start_time_us: 1,
        end_time_us: 2,
        limit: 50,
        items: [MOCK_SQL_ITEM],
      }
    })
    renderPage()

    expect(await screen.findByText("select * from biz_table")).toBeInTheDocument()

    const clusterCombobox = screen.getAllByRole("combobox")[0]
    await user.click(clusterCombobox)
    await user.click(await screen.findByRole("option", { name: "wx" }))

    await waitFor(() => {
      expect(sqlAnalysisApi.listLiveCategory).toHaveBeenLastCalledWith(
        expect.objectContaining({ datasource_id: 7, category: "top_sql", limit: 50 })
      )
    })

    expect(await screen.findByText("select * from wx_table")).toBeInTheDocument()
    expect(screen.queryByText("select * from biz_table")).not.toBeInTheDocument()
    const sqlIdCell = screen.getByText("sql-wx")
    const sqlRow = sqlIdCell.closest("tr")
    expect(sqlRow).not.toBeNull()
  })

  it("switches to AI Analysis mode when AI Analysis tab clicked", async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText("sql-2")
    await user.click(screen.getByText("sql-2"))

    const aiTab = await screen.findByRole("tab", { name: "AI Analysis" })
    expect(aiTab).toBeInTheDocument()

    await user.click(aiTab)

    expect(await screen.findByTestId("conversation-panel")).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "AI Analysis" })).toHaveAttribute("data-state", "active")
    expect(screen.getByText("sql-5-sql-2")).toBeInTheDocument()
    expect(mockSceneAgentChatShell).toHaveBeenLastCalledWith(
      expect.objectContaining({
        datasourceId: 5,
        embeddedInDrawer: true,
        submitSuggestedPrompt: false,
        suggestedPrompt: "Based on current signals, analyze the main risks and investigation suggestions for SQL sql-2",
        freshSessionKey: JSON.stringify({
          datasourceId: 5,
          sceneKey: "sql_analysis",
          focusObject: {
            kind: "sql",
            sql_id: "sql-2",
            db_name: "app_db",
            user_name: "root",
          },
        }),
        adapter: expect.objectContaining({
          sceneKey: "sql-5-sql-2",
          placeholder: "Enter a question to continue analysis...",
          suggestions: [
            "Based on current signals, analyze the main risks of SQL sql-2",
            "If only one direction can be prioritized, what should be checked first for this SQL",
            "Summarize the symptoms, evidence, and next investigation steps for this SQL",
          ],
        }),
      })
    )
  })

  it("switches between detail and chat tabs in drawer", async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText("sql-2")
    await user.click(screen.getByText("sql-2"))

    expect(await screen.findByText("SQL Overview")).toBeInTheDocument()
    expect(screen.queryByTestId("conversation-panel")).not.toBeInTheDocument()

    await user.click(screen.getByRole("tab", { name: "AI Analysis" }))
    expect(await screen.findByTestId("conversation-panel")).toBeInTheDocument()
    expect(screen.queryByText("SQL Overview")).not.toBeInTheDocument()

    await user.click(screen.getByRole("tab", { name: "Details" }))
    expect(await screen.findByText("SQL Overview")).toBeInTheDocument()
    expect(screen.queryByTestId("conversation-panel")).not.toBeInTheDocument()
  })

  it("passes a fresh session key per SQL object to the shared scene shell", async () => {
    const user = userEvent.setup()
    sqlAnalysisApi.listLiveCategory.mockResolvedValue({
      category: "top_sql",
      datasource_id: 5,
      start_time_us: 1,
      end_time_us: 2,
      limit: 50,
      items: [MOCK_SQL_ITEM, MOCK_WX_SQL_ITEM],
    })
    sqlAnalysisApi.buildLiveContext.mockImplementation(async (params: { datasource_id: number; sql_id: string }) => {
      if (params.datasource_id === 7) {
        return {
          ...MOCK_CONTEXT,
          datasource_id: 7,
          sql_id: "sql-wx",
          facts: {
            ...MOCK_CONTEXT.facts,
            datasource_id: 7,
            tenant_id: 1003,
            db_name: "wx_db",
            user_name: "wx_user",
            sql_text: "select * from wx_table",
          },
        }
      }
      return MOCK_CONTEXT
    })

    renderPage()

    await screen.findByText("sql-2")
    await user.click(screen.getByText("sql-2"))
    await user.click(await screen.findByRole("tab", { name: "AI Analysis" }))

    expect(mockSceneAgentChatShell).toHaveBeenLastCalledWith(
      expect.objectContaining({
        freshSessionKey: JSON.stringify({
          datasourceId: 5,
          sceneKey: "sql_analysis",
          focusObject: {
            kind: "sql",
            sql_id: "sql-2",
            db_name: "app_db",
            user_name: "root",
          },
        }),
      })
    )

    await user.click(screen.getByRole("button", { name: "Close" }))
    await user.click(screen.getByText("sql-wx"))
    await user.click(await screen.findByRole("tab", { name: "AI Analysis" }))

    expect(mockSceneAgentChatShell).toHaveBeenLastCalledWith(
      expect.objectContaining({
        datasourceId: 7,
        suggestedPrompt: "Based on current signals, analyze the main risks and investigation suggestions for SQL sql-wx",
        freshSessionKey: JSON.stringify({
          datasourceId: 7,
          sceneKey: "sql_analysis",
          focusObject: {
            kind: "sql",
            sql_id: "sql-wx",
            db_name: "wx_db",
            user_name: "wx_user",
          },
        }),
      })
    )
  })
})
