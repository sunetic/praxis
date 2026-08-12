import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"

import { ShellI18nProvider, SHELL_LOCALE_STORAGE_KEY } from "@/i18n/shellI18n"
import { Topbar } from "./Topbar"

describe("Topbar", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("renders Chinese and toggles to English", async () => {
    render(
      <ShellI18nProvider initialLocale="zh-CN">
        <MemoryRouter initialEntries={["/chat"]}>
          <Topbar />
        </MemoryRouter>
      </ShellI18nProvider>
    )

    expect(screen.getByText("搜索...")).toBeInTheDocument()
    expect(screen.getByText("管理员")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "切换语言" })).toHaveTextContent("EN")

    await userEvent.click(screen.getByRole("button", { name: "切换语言" }))

    expect(screen.getByText("Search...")).toBeInTheDocument()
    expect(screen.getByText("Admin")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Switch language" })).toHaveTextContent("中文")
    expect(window.localStorage.getItem(SHELL_LOCALE_STORAGE_KEY)).toBe("en-US")
  })
})
