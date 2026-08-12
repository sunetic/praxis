import { act, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { renderWithShell as render } from "@/test/renderWithShell"
import { SessionTransactionPage } from "./SessionTransactionPage"

const { datasourcesApi, sessionAnalysisApi, chatApi, conversationsApi, messagesApi, toast } = vi.hoisted(() => ({
  datasourcesApi: {
    list: vi.fn(),
  },
  sessionAnalysisApi: {
    listSessions: vi.fn(),
    listTransactions: vi.fn(),
    killSession: vi.fn(),
    analyzeStream: vi.fn(),
  },
  chatApi: {
    stream: vi.fn(),
    listPendingActions: vi.fn(),
    listEvents: vi.fn(),
  },
  conversationsApi: {
    create: vi.fn(),
    list: vi.fn(),
  },
  messagesApi: {
    list: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  datasourcesApi,
  sessionAnalysisApi,
  chatApi,
  conversationsApi,
  messagesApi,
}))

vi.mock("sonner", () => ({
  toast,
}))

describe("SessionTransactionPage", () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
    datasourcesApi.list.mockResolvedValue([
      {
        id: 8,
        name: "OB WX",
        host: "127.0.0.1",
        port: 2883,
        db_type: "oceanbase",
        cluster_key: "cluster-a",
        tenant_role: "user",
        user: "root@wx",
        database: "oceanbase",
        status: "active",
        attributes: { tenant_id: 1, tenant_name: "wx" },
        created_at: "2026-04-04T00:00:00Z",
        updated_at: "2026-04-04T00:00:00Z",
      },
      {
        id: 7,
        name: "OB Core",
        host: "127.0.0.1",
        port: 2883,
        db_type: "oceanbase",
        cluster_key: "cluster-a",
        tenant_role: "sys",
        user: "root@core",
        database: "oceanbase",
        status: "active",
        attributes: { tenant_id: 2, tenant_name: "core" },
        created_at: "2026-04-04T00:00:00Z",
        updated_at: "2026-04-04T00:00:00Z",
      },
      {
        id: 11,
        name: "OB BIZ B",
        host: "127.0.0.1",
        port: 2883,
        db_type: "oceanbase",
        cluster_key: "cluster-b",
        tenant_role: "user",
        user: "root@wb",
        database: "oceanbase",
        status: "active",
        attributes: { tenant_id: 3, tenant_name: "wb" },
        created_at: "2026-04-04T00:00:00Z",
        updated_at: "2026-04-04T00:00:00Z",
      },
    ])
    sessionAnalysisApi.listSessions.mockResolvedValue({
      datasource_id: null,
      total: 2,
      active: 1,
      sessions: [
        {
          datasource_id: 7,
          session_id: 101,
          user: "root",
          identity_label: "root@core",
          tenant_name: "core",
          client_ip: "10.0.0.8",
          db: "app_db",
          command: "Query",
          time_seconds: 88,
          state: "ACTIVE",
          current_sql: "select * from t_order",
          ob_tenant_id: null,
        },
        {
          datasource_id: 11,
          session_id: 102,
          user: "reader",
          identity_label: "reader@wx",
          tenant_name: "wx",
          client_ip: "10.0.0.9",
          db: "app_db",
          command: "Sleep",
          time_seconds: 12,
          state: "SLEEP",
          current_sql: null,
          ob_tenant_id: null,
        },
      ],
    })
    sessionAnalysisApi.listTransactions.mockResolvedValue({
      datasource_id: null,
      long_transactions: [
        {
          datasource_id: 7,
          trans_hash: "txn-1",
          session_id: 101,
          tenant_id: 1001,
          trans_type: "DISTRIBUTED",
          state: "ACTIVE",
          elapsed_seconds: 120,
          participants: 2,
          sql_list: ["update t_order set status = 1 where id = 1"],
        },
      ],
      pending_transactions: [],
    })
    sessionAnalysisApi.killSession.mockResolvedValue({
      session_id: 101,
      killed: true,
      message: "Session killed",
    })
    sessionAnalysisApi.analyzeStream.mockResolvedValue(
      new Response(' data:{"type":"text","data":"存在长事务，请优先处理。"}\n\n data:{"type":"done"}\n\n')
    )
    chatApi.stream.mockResolvedValue({ ok: true, body: {} as ReadableStream<Uint8Array> })
    chatApi.listPendingActions.mockResolvedValue([])
    chatApi.listEvents.mockResolvedValue([])
    conversationsApi.create.mockResolvedValue({ id: 1, title: "AI 会话诊断" })
    conversationsApi.list.mockResolvedValue([])
    messagesApi.list.mockResolvedValue([])
  })

  it("defaults to active sessions, can switch to all, and shows txn sql samples in drawer", async () => {
    const user = userEvent.setup()

    render(<SessionTransactionPage />)

    await screen.findAllByText("root@core")

    // Default cluster is ALL_CLUSTERS ("全部集群") and should query global scope
    const clusterSelect = screen.getByRole("combobox", { name: "集群筛选" })
    expect(clusterSelect).toHaveTextContent("全部集群")
    expect(screen.getAllByText("root@core").length).toBeGreaterThan(0)
    expect(screen.getAllByText("101").length).toBeGreaterThan(0)
    expect(screen.queryByText("102")).not.toBeInTheDocument()

    // Switch to "全部会话"
    await user.click(screen.getByRole("tab", { name: "全部会话" }))
    expect(await screen.findByText("102")).toBeInTheDocument()

    // Open transaction drawer
    await user.click(screen.getByText("txn-1…"))
    expect(await screen.findByText("事务 SQL 样本")).toBeInTheDocument()
    expect(screen.getAllByText("update t_order set status = 1 where id = 1").length).toBeGreaterThan(1)

    await waitFor(() => {
      expect(sessionAnalysisApi.analyzeStream).toHaveBeenCalled()
    })

    await user.click(screen.getByRole("button", { name: "关闭" }))

    // Switch cluster from ALL_CLUSTERS to "cluster-a"
    await user.click(clusterSelect)
    await user.click(await screen.findByRole("option", { name: "cluster-a" }))
    expect(await screen.findByRole("combobox", { name: "集群筛选" })).toHaveTextContent("cluster-a")

    // Switch cluster from "cluster-a" to "cluster-b"
    await user.click(screen.getByRole("combobox", { name: "集群筛选" }))
    await user.click(await screen.findByRole("option", { name: "cluster-b" }))
    expect(await screen.findByRole("combobox", { name: "集群筛选" })).toHaveTextContent("cluster-b")

    expect(sessionAnalysisApi.listSessions).toHaveBeenCalledWith({ datasource_id: null, cluster_key: null })
    expect(sessionAnalysisApi.listTransactions).toHaveBeenCalledWith({ datasource_id: null, cluster_key: null })
    expect(sessionAnalysisApi.listSessions).toHaveBeenCalledWith({ datasource_id: null, cluster_key: "cluster-a" })
    expect(sessionAnalysisApi.listTransactions).toHaveBeenCalledWith({ datasource_id: null, cluster_key: "cluster-a" })
    expect(sessionAnalysisApi.listSessions).toHaveBeenCalledWith({ datasource_id: null, cluster_key: "cluster-b" })
    expect(sessionAnalysisApi.listTransactions).toHaveBeenCalledWith({ datasource_id: null, cluster_key: "cluster-b" })
  })

  it("uses datasource scope when a concrete datasource is selected", async () => {
    const user = userEvent.setup()

    render(<SessionTransactionPage />)

    await screen.findAllByText("root@core")
    await user.click(screen.getByRole("combobox", { name: "数据源筛选" }))
    await user.click(await screen.findByRole("option", { name: "OB BIZ B" }))

    await waitFor(() => {
      expect(sessionAnalysisApi.listSessions).toHaveBeenLastCalledWith({ datasource_id: 11, cluster_key: null, tenant_id: 3, tenant_name: "wb" })
      expect(sessionAnalysisApi.listTransactions).toHaveBeenLastCalledWith({ datasource_id: 11, cluster_key: null, tenant_id: 3, tenant_name: "wb" })
    })
  })

  it("auto refreshes by default and can be disabled", async () => {
    const user = userEvent.setup()
    const setIntervalSpy = vi.spyOn(window, "setInterval")
    const clearIntervalSpy = vi.spyOn(window, "clearInterval")

    render(<SessionTransactionPage />)

    await screen.findAllByText("root@core")
    await waitFor(() => {
      expect(sessionAnalysisApi.listSessions).toHaveBeenCalledTimes(1)
      expect(sessionAnalysisApi.listTransactions).toHaveBeenCalledTimes(1)
      expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 30_000)
    })

    const refreshCallback = setIntervalSpy.mock.calls.find(([, delay]) => delay === 30_000)?.[0]
    expect(typeof refreshCallback).toBe("function")

    await act(async () => {
      ;(refreshCallback as () => void)()
    })

    await waitFor(() => {
      expect(sessionAnalysisApi.listSessions).toHaveBeenCalledTimes(2)
      expect(sessionAnalysisApi.listTransactions).toHaveBeenCalledTimes(2)
    })

    await user.click(screen.getByRole("switch", { name: "自动刷新开关" }))

    expect(screen.getByRole("combobox", { name: "自动刷新频率" })).toBeDisabled()
    expect(clearIntervalSpy).toHaveBeenCalled()
  })

  it("uses the selected auto refresh interval", async () => {
    const user = userEvent.setup()
    const setIntervalSpy = vi.spyOn(window, "setInterval")
    const clearIntervalSpy = vi.spyOn(window, "clearInterval")

    render(<SessionTransactionPage />)

    await screen.findAllByText("root@core")
    await waitFor(() => {
      expect(sessionAnalysisApi.listSessions).toHaveBeenCalledTimes(1)
      expect(sessionAnalysisApi.listTransactions).toHaveBeenCalledTimes(1)
      expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 30_000)
    })

    await user.click(screen.getByRole("combobox", { name: "自动刷新频率" }))
    await user.click(await screen.findByRole("option", { name: "15 秒" }))

    await waitFor(() => {
      expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 15_000)
      expect(clearIntervalSpy).toHaveBeenCalled()
    })

    const refreshCallback = [...setIntervalSpy.mock.calls].reverse().find(([, delay]) => delay === 15_000)?.[0]
    expect(typeof refreshCallback).toBe("function")

    await act(async () => {
      ;(refreshCallback as () => void)()
    })

    await waitFor(() => {
      expect(sessionAnalysisApi.listSessions).toHaveBeenCalledTimes(2)
      expect(sessionAnalysisApi.listTransactions).toHaveBeenCalledTimes(2)
    })
  })

  it("opens AI diagnosis drawer when AI 诊断 button is clicked", async () => {
    const user = userEvent.setup()

    render(<SessionTransactionPage />)

    await screen.findAllByText("root@core")

    const aiButton = screen.getByRole("button", { name: "AI 诊断" })
    expect(aiButton).toBeInTheDocument()
    await user.click(aiButton)

    expect(await screen.findByText("AI 会话诊断")).toBeInTheDocument()
  })

  it("still opens AI drawer even when transaction data fails to load", async () => {
    const user = userEvent.setup()
    sessionAnalysisApi.listTransactions.mockRejectedValueOnce(new Error("boom"))

    render(<SessionTransactionPage />)

    await screen.findAllByText("root@core")
    await user.click(screen.getByRole("button", { name: "AI 诊断" }))

    expect(await screen.findByText("AI 会话诊断")).toBeInTheDocument()
  })
})
