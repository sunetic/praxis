import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { renderWithShell as render } from "@/test/renderWithShell"
import { SettingsPage } from "./SettingsPage"

const { settingsApi } = vi.hoisted(() => ({
  settingsApi: {
    get: vi.fn(),
    patch: vi.fn(),
    testEngine: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({ settingsApi }))

describe("SettingsPage build engine", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    settingsApi.get.mockResolvedValue({
      build_engine: "external_cli",
      external_cli_command: "claude",
    })
    settingsApi.patch.mockResolvedValue({})
  })

  it("applies a validated command suggested by the backend", async () => {
    settingsApi.testEngine.mockResolvedValue({
      ok: true,
      message: "连接成功",
      suggested_command: "claude -p --output-format json",
      flags_added: ["-p", "--output-format json"],
      env_issues: [],
    })
    const user = userEvent.setup()

    render(<SettingsPage />)
    await user.click(screen.getByRole("tab", { name: "构建引擎" }))
    const command = await screen.findByLabelText("CLI 命令")
    await user.click(screen.getByRole("button", { name: "测试连接" }))

    await waitFor(() => expect(settingsApi.testEngine).toHaveBeenCalledWith("claude"))
    expect(command).toHaveValue("claude -p --output-format json")
    expect(screen.getByText("连接成功")).toBeInTheDocument()
  })

  it("keeps the configured command when validation has no suggestion", async () => {
    settingsApi.testEngine.mockResolvedValue({
      ok: true,
      message: "连接成功",
      env_issues: [],
    })
    const user = userEvent.setup()

    render(<SettingsPage />)
    await user.click(screen.getByRole("tab", { name: "构建引擎" }))
    const command = await screen.findByLabelText("CLI 命令")
    await user.clear(command)
    await user.type(command, "cursor --cli")
    await user.click(screen.getByRole("button", { name: "测试连接" }))

    await waitFor(() => expect(settingsApi.testEngine).toHaveBeenCalledWith("cursor --cli"))
    expect(command).toHaveValue("cursor --cli")
  })
})
