import { screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"

import { renderWithShell as render } from "@/test/renderWithShell"
import { Sidebar } from "./Sidebar"

describe("Sidebar", () => {
  it("renders the current grouped workspace entries", () => {
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

    expect(within(connectivity).getByRole("link", { name: "数据源" })).toBeInTheDocument()
    expect(within(connectivity).getByRole("link", { name: "服务" })).toBeInTheDocument()
    expect(within(connectivity).getByRole("link", { name: "知识库" })).toBeInTheDocument()
    expect(within(connectivity).getByRole("link", { name: "渠道" })).toBeInTheDocument()

    expect(within(buildAndRuntime).getByRole("link", { name: "函数" })).toBeInTheDocument()
    expect(within(buildAndRuntime).getByRole("link", { name: "调度" })).toBeInTheDocument()

    expect(screen.getByRole("link", { name: "函数" })).toHaveClass("bg-indigo-50")
  })

  it("keeps hidden page and SQL-analysis entries out of navigation", () => {
    render(
      <MemoryRouter initialEntries={["/page"]}>
        <Sidebar />
      </MemoryRouter>
    )

    expect(screen.queryByRole("link", { name: "页面" })).not.toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "SQL 分析" })).not.toBeInTheDocument()
  })
})
