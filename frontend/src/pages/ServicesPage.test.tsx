import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ServicesPage } from "./ServicesPage"

const { servicesApi, datasourcesApi, toast } = vi.hoisted(() => ({
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
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  servicesApi,
  datasourcesApi,
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
        service_type: "ocp_api",
        resource_ref: "cluster:cluster-a",
        config: null,
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 2,
        name: "datasource-service",
        service_type: "ocp_api",
        resource_ref: "datasource:101",
        config: null,
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 3,
        name: "stale-cluster-service",
        service_type: "ocp_api",
        resource_ref: "cluster:stale-cluster",
        config: null,
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 4,
        name: "stale-datasource-service",
        service_type: "ocp_api",
        resource_ref: "datasource:999",
        config: null,
        status: "active",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:00:00Z",
      },
      {
        id: 5,
        name: "unbound-service",
        service_type: "ocp_api",
        resource_ref: null,
        config: null,
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
    expect(within(staleClusterRow as HTMLElement).getByText("无")).toBeInTheDocument()
    expect(within(staleDatasourceRow as HTMLElement).getByText("无")).toBeInTheDocument()
    expect(within(unboundRow as HTMLElement).getByText("无")).toBeInTheDocument()
  })

  it("filters by resolved relation resource instead of raw resource_ref", async () => {
    const user = userEvent.setup()
    render(<ServicesPage />)

    await screen.findByText("cluster-service")

    await user.type(screen.getByPlaceholderText("搜索服务..."), "orders-ds")

    await waitFor(() => {
      expect(screen.getByText("datasource-service")).toBeInTheDocument()
    })

    expect(screen.queryByText("cluster-service")).not.toBeInTheDocument()
    expect(screen.queryByText("stale-datasource-service")).not.toBeInTheDocument()
  })
})
