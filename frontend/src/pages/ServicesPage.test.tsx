import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ServicesPage } from "./ServicesPage"

const { servicesApi, datasourcesApi, knowledgeApi, toast } = vi.hoisted(() => ({
  servicesApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    test: vi.fn(),
    testConfig: vi.fn(),
  },
  datasourcesApi: {
    list: vi.fn(),
  },
  knowledgeApi: {
    list: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  servicesApi,
  datasourcesApi,
  knowledgeApi,
}))

vi.mock("sonner", () => ({
  toast,
}))

describe("ServicesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()

    servicesApi.list.mockResolvedValue([
      {
        id: 1,
        name: "cluster-service",
        service_type: "prometheus",
        resource_ref: "cluster:cluster-a",
        config: { base_url: "http://prometheus:9090" },
        has_credentials: false,
        knowledge_base_ids: [],
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 2,
        name: "datasource-service",
        service_type: "prometheus",
        resource_ref: "datasource:101",
        config: { base_url: "http://prometheus:9090" },
        has_credentials: false,
        knowledge_base_ids: [],
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 3,
        name: "stale-cluster-service",
        service_type: "prometheus",
        resource_ref: "cluster:stale-cluster",
        config: { base_url: "http://prometheus:9090" },
        has_credentials: false,
        knowledge_base_ids: [],
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 4,
        name: "stale-datasource-service",
        service_type: "prometheus",
        resource_ref: "datasource:999",
        config: { base_url: "http://prometheus:9090" },
        has_credentials: false,
        knowledge_base_ids: [],
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 5,
        name: "unbound-service",
        service_type: "prometheus",
        resource_ref: null,
        config: { base_url: "http://prometheus:9090" },
        has_credentials: false,
        knowledge_base_ids: [],
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
    ])

    datasourcesApi.list.mockResolvedValue([
      {
        id: 101,
        name: "orders-ds",
        host: "127.0.0.1",
        port: 2881,
        db_type: "oceanbase",
        cluster_key: "cluster-a",
        tenant_role: "user",
        user: "tenant",
        database: "orders",
        attributes: null,
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
    ])
    knowledgeApi.list.mockResolvedValue([])
  })

  it("renders valid relation resources and hides stale references", async () => {
    render(<ServicesPage />)

    await screen.findByText("cluster-service")

    const clusterRow = screen.getByText("cluster-service").closest("tr")
    const datasourceRow = screen.getByText("datasource-service").closest("tr")
    const staleClusterRow = screen.getByText("stale-cluster-service").closest("tr")
    const staleDatasourceRow = screen.getByText("stale-datasource-service").closest("tr")
    const unboundRow = screen.getByText("unbound-service").closest("tr")

    expect(clusterRow).not.toBeNull()
    expect(datasourceRow).not.toBeNull()
    expect(staleClusterRow).not.toBeNull()
    expect(staleDatasourceRow).not.toBeNull()
    expect(unboundRow).not.toBeNull()

    expect(within(clusterRow as HTMLElement).getByText("cluster-a")).toBeInTheDocument()
    expect(within(datasourceRow as HTMLElement).getByText("orders-ds")).toBeInTheDocument()
    expect(within(staleClusterRow as HTMLElement).getByText("引用已失效")).toBeInTheDocument()
    expect(within(staleDatasourceRow as HTMLElement).getByText("引用已失效")).toBeInTheDocument()
    expect(within(unboundRow as HTMLElement).getByText("未关联")).toBeInTheDocument()
  })

  it("filters by resolved relation resource instead of raw resource_ref", async () => {
    const user = userEvent.setup()
    render(<ServicesPage />)

    await screen.findByText("cluster-service")

    await user.type(screen.getByPlaceholderText("搜索名称、地址或关联资源"), "orders-ds")

    await waitFor(() => {
      expect(screen.getByText("datasource-service")).toBeInTheDocument()
    })

    expect(screen.queryByText("cluster-service")).not.toBeInTheDocument()
    expect(screen.queryByText("stale-datasource-service")).not.toBeInTheDocument()
  })

  it("keeps the edit form scrollable inside a wide dialog", async () => {
    const user = userEvent.setup()
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView })
    render(<ServicesPage />)

    await screen.findByText("cluster-service")
    const clusterRow = screen.getByText("cluster-service").closest("tr")
    expect(clusterRow).not.toBeNull()
    await user.click(within(clusterRow as HTMLElement).getByRole("button", { name: "编辑服务" }))

    const dialog = await screen.findByRole("dialog", { name: "编辑外部服务" })
    expect(dialog).toHaveClass("h-[90vh]", "max-h-192", "sm:max-w-2xl")
    expect(dialog.querySelector('[data-slot="scroll-area"]')).toHaveClass(
      "h-0",
      "min-h-0",
      "flex-1",
      "[&>[data-slot=scroll-area-viewport]]:absolute",
      "[&>[data-slot=scroll-area-viewport]]:inset-0",
    )

    await user.click(within(dialog).getByRole("button", { name: "高级 Header 配置" }))
    const defaultHeaders = within(dialog).getByLabelText("默认 Header（JSON）")
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" }))
    await user.clear(defaultHeaders)
    await user.type(defaultHeaders, '"X-Test":"visible"')
    expect(defaultHeaders).toHaveValue('"X-Test":"visible"')
  })
})
