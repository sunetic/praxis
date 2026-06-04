import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi, beforeEach } from "vitest"
import { MemoryRouter } from "react-router-dom"

import { Sidebar } from "./Sidebar"

const { pagesApi } = vi.hoisted(() => ({
  pagesApi: {
    navigation: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  pagesApi,
}))

describe("Sidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pagesApi.navigation.mockResolvedValue([
      { id: 1, name: "首页概览", status: "published", path: "/page/1" },
      { id: 2, name: "慢 SQL 看板", status: "published", path: "/page/2" },
    ])
  })

  it("renders grouped workspace entries and preserves page expansion", async () => {
    render(
      <MemoryRouter initialEntries={["/function"]}>
        <Sidebar />
      </MemoryRouter>
    )

    const aiWorkspace = screen.getByRole("group", { name: "AI 工作台" })
    const connectivity = screen.getByRole("group", { name: "连接管理" })
    const buildAndRuntime = screen.getByRole("group", { name: "构建与运行" })

    expect(within(aiWorkspace).getByRole("link", { name: "对话" })).toBeInTheDocument()
    expect(within(aiWorkspace).getByRole("link", { name: "智能体" })).toBeInTheDocument()
    expect(within(aiWorkspace).getByRole("link", { name: "技能" })).toBeInTheDocument()
    expect(within(aiWorkspace).getByRole("link", { name: "SQL 分析" })).toBeInTheDocument()

    expect(within(connectivity).getByRole("link", { name: "数据源" })).toBeInTheDocument()
    expect(within(connectivity).getByRole("link", { name: "渠道" })).toBeInTheDocument()

    expect(within(buildAndRuntime).getByRole("link", { name: "函数" })).toBeInTheDocument()
    expect(within(buildAndRuntime).getByRole("link", { name: "调度" })).toBeInTheDocument()
    expect(within(buildAndRuntime).getByRole("link", { name: "页面" })).toBeInTheDocument()

    expect(screen.getByRole("link", { name: "函数" })).toHaveClass("bg-indigo-50")
    expect(screen.queryByText("首页概览")).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole("link", { name: "页面" }))
    await waitFor(() => expect(screen.getByText("首页概览")).toBeInTheDocument())
  })

  it("keeps the page group visible when no published page exists", async () => {
    pagesApi.navigation.mockResolvedValue([])

    render(
      <MemoryRouter initialEntries={["/page"]}>
        <Sidebar />
      </MemoryRouter>
    )

    const buildAndRuntime = screen.getByRole("group", { name: "构建与运行" })
    expect(within(buildAndRuntime).getByRole("link", { name: "页面" })).toBeInTheDocument()

    await waitFor(() => expect(screen.getByText("暂无已发布页面")).toBeInTheDocument())
  })
})
