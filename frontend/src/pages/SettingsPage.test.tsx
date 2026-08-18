import { fireEvent, screen, waitFor } from "@testing-library/react"
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
      ai_api_key: "sk-test",
      ai_model: "gpt-test",
      context_window_tokens: 128000,
      context_compression_threshold_percent: 75,
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

  it("shows the mainstream context defaults and saves a custom trigger", async () => {
    const user = userEvent.setup()
    render(<SettingsPage />)

    const windowInput = await screen.findByLabelText("模型上下文窗口")
    const thresholdInput = screen.getByLabelText("自动压缩阈值")
    expect(windowInput).toHaveValue(128000)
    expect(thresholdInput).toHaveValue(75)
    expect(screen.getByText(/96,000 tokens/)).toBeInTheDocument()

    fireEvent.change(windowInput, { target: { value: "200000" } })
    fireEvent.change(thresholdInput, { target: { value: "80" } })
    await user.click(screen.getByRole("button", { name: "保存" }))

    await waitFor(() => expect(settingsApi.patch).toHaveBeenCalledWith(expect.objectContaining({
      context_window_tokens: 200000,
      context_compression_threshold_percent: 80,
    })))
  })

  it("blocks saving an unsafe context threshold", async () => {
    const user = userEvent.setup()
    render(<SettingsPage />)
    const thresholdInput = await screen.findByLabelText("自动压缩阈值")

    fireEvent.change(thresholdInput, { target: { value: "49" } })

    expect(screen.getByText(/阈值需为 50%–95%/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled()
  })

  it("allows context settings to be saved for a keyless local model", async () => {
    settingsApi.get.mockResolvedValueOnce({
      ai_api_key: "",
      ai_model: "local-model",
      ai_base_url: "http://127.0.0.1:11434/v1",
      context_window_tokens: 128000,
      context_compression_threshold_percent: 75,
    })
    const user = userEvent.setup()
    render(<SettingsPage />)

    const save = await screen.findByRole("button", { name: "保存" })
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() => expect(settingsApi.patch).toHaveBeenCalledWith(expect.objectContaining({
      ai_api_key: "",
      context_window_tokens: 128000,
      context_compression_threshold_percent: 75,
    })))
  })
})
