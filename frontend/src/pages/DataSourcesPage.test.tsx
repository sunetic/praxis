import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { renderWithShell as render } from "@/test/renderWithShell"
import { DataSourcesPage } from "./DataSourcesPage"

const { datasourcesApi, toast } = vi.hoisted(() => ({
  datasourcesApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    test: vi.fn(),
    testById: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  datasourcesApi,
}))

vi.mock("sonner", () => ({
  toast,
}))

describe("DataSourcesPage monitor datasource baseline", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    datasourcesApi.list.mockResolvedValue([
      {
        id: 1,
        name: "live-a",
        host: "127.0.0.1",
        port: 2881,
        db_type: "oceanbase",
        cluster_key: "cluster-a",
        tenant_role: "user",
        user: "tenant",
        database: "test",
        attributes: null,
        status: "active",
        created_at: "2026-03-28T00:00:00Z",
        updated_at: "2026-03-28T00:00:00Z",
      },
      {
        id: 2,
        name: "live-b",
        host: "10.0.0.1",
        port: 2881,
        db_type: "oceanbase",
        cluster_key: "cluster-b",
        tenant_role: "user",
        user: "root",
        database: "oceanbase",
        attributes: { region: "cn-hangzhou" },
        status: "active",
        created_at: "2026-03-28T00:00:00Z",
        updated_at: "2026-03-28T00:00:00Z",
      },
    ])
  })

  it("opens create dialog with form fields", async () => {
    const user = userEvent.setup()
    render(<DataSourcesPage />)

    await screen.findByText("live-a")
    await user.click(screen.getByRole("button", { name: /(新建|添加)数据源/i }))

    expect(screen.getByText("添加数据源")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("my-database")).toBeInTheDocument()
  })

  it("edit button works when attributes is null", async () => {
    const user = userEvent.setup()
    render(<DataSourcesPage />)

    await screen.findByText("live-a")
    const editButtons = screen.getAllByRole("button", { name: "编辑" })
    await user.click(editButtons[0])

    expect(screen.getByText("编辑数据源")).toBeInTheDocument()
    expect(screen.getByDisplayValue("live-a")).toBeInTheDocument()
    expect(screen.getByDisplayValue("127.0.0.1")).toBeInTheDocument()
  })

  it("edit button works when attributes has values", async () => {
    const user = userEvent.setup()
    render(<DataSourcesPage />)

    await screen.findByText("live-b")
    const editButtons = screen.getAllByRole("button", { name: "编辑" })
    await user.click(editButtons[1])

    expect(screen.getByText("编辑数据源")).toBeInTheDocument()
    expect(screen.getByDisplayValue("live-b")).toBeInTheDocument()
    expect(screen.getByDisplayValue("10.0.0.1")).toBeInTheDocument()
  })
})
