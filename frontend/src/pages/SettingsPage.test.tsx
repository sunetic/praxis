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
      ai_api_key_configured: true,
      ai_model: "gpt-test",
      sql_allow_mutating: true,
      ai_action_confirmation_bypass: false,
      context_window_tokens: 128000,
      context_compression_threshold_percent: 75,
    })
    settingsApi.patch.mockResolvedValue({ ai_api_key_configured: true })
  })

  it("shows the shared Reasoning Engine as the built-in option", async () => {
    settingsApi.get.mockResolvedValue({
      build_engine: "reasoning",
      external_cli_command: "",
      ai_api_key_configured: true,
      context_window_tokens: 128000,
      context_compression_threshold_percent: 75,
    })
    const user = userEvent.setup()

    render(<SettingsPage />)
    await user.click(screen.getByRole("tab", { name: "构建引擎" }))

    expect(await screen.findByText("内建 Reasoning Engine")).toBeInTheDocument()
    expect(screen.getByText(/Chat、Function、Page 共用同一套推理与工具循环核心/)).toBeInTheDocument()
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
    expect(settingsApi.patch.mock.calls[0][0]).not.toHaveProperty("ai_api_key")
  })

  it("blocks saving an unsafe context threshold", async () => {
    render(<SettingsPage />)
    const thresholdInput = await screen.findByLabelText("自动压缩阈值")

    fireEvent.change(thresholdInput, { target: { value: "49" } })

    expect(screen.getByText(/阈值需为 50%–95%/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled()
  })

  it("allows context settings to be saved for a keyless local model", async () => {
    settingsApi.get.mockResolvedValueOnce({
      ai_api_key_configured: false,
      ai_model: "local-model",
      ai_base_url: "http://127.0.0.1:11434/v1",
      context_window_tokens: 128000,
      context_compression_threshold_percent: 75,
    })
    settingsApi.patch.mockResolvedValueOnce({ ai_api_key_configured: false })
    const user = userEvent.setup()
    render(<SettingsPage />)

    const save = await screen.findByRole("button", { name: "保存" })
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() => expect(settingsApi.patch).toHaveBeenCalledWith(expect.objectContaining({
      context_window_tokens: 128000,
      context_compression_threshold_percent: 75,
    })))
    expect(settingsApi.patch.mock.calls[0][0]).not.toHaveProperty("ai_api_key")
  })

  it("keeps a configured API key hidden until it is replaced", async () => {
    const user = userEvent.setup()
    render(<SettingsPage />)

    const apiKey = await screen.findByLabelText("API Key")
    expect(apiKey).toHaveValue("")
    expect(apiKey).toHaveAttribute("placeholder", "已配置，输入新值以替换")

    await user.type(apiKey, "sk-replacement")
    await user.click(screen.getByRole("button", { name: "保存" }))

    await waitFor(() => expect(settingsApi.patch).toHaveBeenCalledWith(expect.objectContaining({
      ai_api_key: "sk-replacement",
    })))
  })

  it("enables AI action confirmation bypass from the safety tab", async () => {
    const user = userEvent.setup()
    render(<SettingsPage />)

    await user.click(screen.getByRole("tab", { name: "安全" }))
    const bypass = await screen.findByRole("switch", { name: "Bypass 模式（跳过确认）" })
    expect(bypass).not.toBeChecked()
    expect(screen.getByText("变更需确认")).toBeInTheDocument()

    await user.click(bypass)

    await waitFor(() => expect(settingsApi.patch).toHaveBeenCalledWith({
      ai_action_confirmation_bypass: true,
    }))
    expect(screen.getByText("自动执行变更")).toBeInTheDocument()
  })

  it("restores the bypass switch when saving fails", async () => {
    settingsApi.patch.mockRejectedValueOnce(new Error("save failed"))
    const user = userEvent.setup()
    render(<SettingsPage />)

    await user.click(screen.getByRole("tab", { name: "安全" }))
    const bypass = await screen.findByRole("switch", { name: "Bypass 模式（跳过确认）" })
    await user.click(bypass)

    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败，设置已恢复。请重试。")
    expect(bypass).not.toBeChecked()
  })
})
