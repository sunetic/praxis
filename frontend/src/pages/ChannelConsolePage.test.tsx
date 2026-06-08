import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes } from "react-router-dom"

import { ChannelConsolePage } from "./ChannelConsolePage"

const { channelsApi } = vi.hoisted(() => ({
  channelsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    sendTest: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  channelsApi,
}))

describe("ChannelConsolePage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    channelsApi.list.mockResolvedValue([])
    channelsApi.create.mockResolvedValue({
      id: 1,
      name: "Alert Bot",
      provider: "dingtalk",
      status: "active",
      config: {
        webhook_url: "https://oapi.dingtalk.com/robot/send?access_token=abc123token",
      },
      created_at: "2026-03-25T12:00:00Z",
      updated_at: "2026-03-25T12:00:00Z",
    })
    channelsApi.update.mockResolvedValue({
      id: 1,
      name: "Alert Bot",
      provider: "dingtalk",
      status: "active",
      config: {
        webhook_url: "https://oapi.dingtalk.com/robot/send?access_token=abc123token",
      },
      created_at: "2026-03-25T12:00:00Z",
      updated_at: "2026-03-25T12:00:00Z",
    })
    channelsApi.sendTest.mockResolvedValue({ object_type: "channel", action: "send" })
  })

  it("shows channel cards including Slack and Telegram", async () => {
    render(
      <MemoryRouter initialEntries={["/channel"]}>
        <Routes>
          <Route path="/channel" element={<ChannelConsolePage />} />
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByText(/Manage IM notification channels/)).toBeInTheDocument()
    await waitFor(() => expect(channelsApi.list).toHaveBeenCalled())
    expect(screen.getByRole("button", { name: "Configure DingTalk" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Configure Slack" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Configure Telegram" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Coming soon Feishu" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Coming soon WeCom" })).toBeInTheDocument()
  })

  it("enters dingtalk wizard from card", async () => {
    render(
      <MemoryRouter initialEntries={["/channel"]}>
        <Routes>
          <Route path="/channel" element={<ChannelConsolePage />} />
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => expect(channelsApi.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole("button", { name: "Configure DingTalk" }))
    expect(await screen.findByText("Basic Info")).toBeInTheDocument()
  })

  it("validates webhook token and security secret in wizard", async () => {
    render(
      <MemoryRouter initialEntries={["/channel/dingtalk"]}>
        <Routes>
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(screen.getByLabelText("DingTalk Webhook")).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText("DingTalk Webhook"), "https://oapi.dingtalk.com/robot/send")
    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(screen.getByLabelText("DingTalk Webhook")).toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText("DingTalk Webhook"))
    await userEvent.type(
      screen.getByLabelText("DingTalk Webhook"),
      "https://oapi.dingtalk.com/robot/send?access_token=abc123token"
    )
    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByLabelText("Sign Secret (starts with SEC)")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(screen.getByLabelText("Sign Secret (starts with SEC)")).toBeInTheDocument()
  })

  it("saves dingtalk config via backend api and returns to channel list", async () => {
    render(
      <MemoryRouter initialEntries={["/channel/dingtalk"]}>
        <Routes>
          <Route path="/channel" element={<ChannelConsolePage />} />
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    await userEvent.clear(screen.getByLabelText("DingTalk Webhook"))
    await userEvent.type(
      screen.getByLabelText("DingTalk Webhook"),
      "https://oapi.dingtalk.com/robot/send?access_token=abc123token"
    )
    await userEvent.click(screen.getByRole("button", { name: "Next" }))

    await userEvent.type(screen.getByLabelText("Sign Secret (starts with SEC)"), "SEC_TEST_VALUE")
    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByLabelText("Message Type")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Save Config" }))
    await waitFor(() => expect(channelsApi.create).toHaveBeenCalled())
    expect(await screen.findByText(/Manage IM notification channels/)).toBeInTheDocument()
  })

  it("enters slack wizard and renders webhook form", async () => {
    render(
      <MemoryRouter initialEntries={["/channel/slack"]}>
        <Routes>
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByLabelText("Slack Webhook URL")).toBeInTheDocument()
    expect(screen.getByLabelText(/Bot Display Name/)).toBeInTheDocument()
  })

  it("saves slack config via backend api", async () => {
    channelsApi.create.mockResolvedValue({
      id: 2,
      name: "Slack Alert",
      provider: "slack",
      status: "active",
      config: { webhook_url: "https://hooks.slack.com/services/T00/B00/xxx" },
      created_at: "2026-03-25T12:00:00Z",
      updated_at: "2026-03-25T12:00:00Z",
    })

    render(
      <MemoryRouter initialEntries={["/channel/slack"]}>
        <Routes>
          <Route path="/channel" element={<ChannelConsolePage />} />
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    await userEvent.clear(screen.getByLabelText("Channel Name"))
    await userEvent.type(screen.getByLabelText("Channel Name"), "Slack Alert")
    await userEvent.type(screen.getByLabelText("Slack Webhook URL"), "https://hooks.slack.com/services/T00/B00/xxx")
    await userEvent.click(screen.getByRole("button", { name: "Next" }))

    expect(await screen.findByLabelText("Message Body")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Save Config" }))
    await waitFor(() => expect(channelsApi.create).toHaveBeenCalled())
  })

  it("enters telegram wizard and renders bot token form", async () => {
    render(
      <MemoryRouter initialEntries={["/channel/telegram"]}>
        <Routes>
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByLabelText("Bot Token")).toBeInTheDocument()
    expect(screen.getByLabelText("Chat ID")).toBeInTheDocument()
  })

  it("saves telegram config via backend api", async () => {
    channelsApi.create.mockResolvedValue({
      id: 3,
      name: "Telegram Alert",
      provider: "telegram",
      status: "active",
      config: { bot_token: "123:ABC", chat_id: "-100123" },
      created_at: "2026-03-25T12:00:00Z",
      updated_at: "2026-03-25T12:00:00Z",
    })

    render(
      <MemoryRouter initialEntries={["/channel/telegram"]}>
        <Routes>
          <Route path="/channel" element={<ChannelConsolePage />} />
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    await userEvent.clear(screen.getByLabelText("Channel Name"))
    await userEvent.type(screen.getByLabelText("Channel Name"), "Telegram Alert")
    await userEvent.type(screen.getByLabelText("Bot Token"), "123:ABC")
    await userEvent.type(screen.getByLabelText("Chat ID"), "-100123")
    await userEvent.click(screen.getByRole("button", { name: "Next" }))

    expect(await screen.findByLabelText("Message Body")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Save Config" }))
    await waitFor(() => expect(channelsApi.create).toHaveBeenCalled())
  })
})
