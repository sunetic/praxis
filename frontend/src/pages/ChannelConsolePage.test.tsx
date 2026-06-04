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
      name: "钉钉通知",
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
      name: "钉钉通知",
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

  it("shows channel cards and enters dingtalk wizard", async () => {
    render(
      <MemoryRouter initialEntries={["/channel"]}>
        <Routes>
          <Route path="/channel" element={<ChannelConsolePage />} />
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByText(/管理 IM 通知通道/)).toBeInTheDocument()
    await waitFor(() => expect(channelsApi.list).toHaveBeenCalled())
    expect(screen.getByRole("button", { name: "配置钉钉" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "即将支持飞书" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "即将支持企业微信" })).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "配置钉钉" }))
    expect(await screen.findByText("基础信息")).toBeInTheDocument()
  })

  it("validates webhook token and security secret in wizard", async () => {
    render(
      <MemoryRouter initialEntries={["/channel/dingtalk"]}>
        <Routes>
          <Route path="/channel/:provider" element={<ChannelConsolePage />} />
        </Routes>
      </MemoryRouter>
    )

    await userEvent.click(screen.getByRole("button", { name: "下一步" }))
    expect(screen.getByLabelText("钉钉 Webhook")).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText("钉钉 Webhook"), "https://oapi.dingtalk.com/robot/send")
    await userEvent.click(screen.getByRole("button", { name: "下一步" }))
    expect(screen.getByLabelText("钉钉 Webhook")).toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText("钉钉 Webhook"))
    await userEvent.type(
      screen.getByLabelText("钉钉 Webhook"),
      "https://oapi.dingtalk.com/robot/send?access_token=abc123token"
    )
    await userEvent.click(screen.getByRole("button", { name: "下一步" }))
    expect(await screen.findByLabelText("加签密钥（SEC 开头）")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "下一步" }))
    expect(screen.getByLabelText("加签密钥（SEC 开头）")).toBeInTheDocument()
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

    await userEvent.clear(screen.getByLabelText("钉钉 Webhook"))
    await userEvent.type(
      screen.getByLabelText("钉钉 Webhook"),
      "https://oapi.dingtalk.com/robot/send?access_token=abc123token"
    )
    await userEvent.click(screen.getByRole("button", { name: "下一步" }))

    await userEvent.type(screen.getByLabelText("加签密钥（SEC 开头）"), "SEC_TEST_VALUE")
    await userEvent.click(screen.getByRole("button", { name: "下一步" }))
    expect(await screen.findByLabelText("消息类型")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "保存配置" }))
    await waitFor(() => expect(channelsApi.create).toHaveBeenCalled())
    expect(await screen.findByText(/管理 IM 通知通道/)).toBeInTheDocument()
  })
})
