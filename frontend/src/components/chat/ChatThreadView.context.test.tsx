import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { renderWithShell as render } from "@/test/renderWithShell"
import { ContextCompressionBanner, ContextUsageIndicator } from "./ChatThreadView"

describe("Chat context visibility", () => {
  it("always shows exact context usage and the configured trigger", () => {
    render(
      <ContextUsageIndicator
        status={{
          conversation_id: 7,
          context_window_tokens: 128000,
          estimated_tokens: 64000,
          used_percent: 50,
          compression_progress_percent: 66.7,
          compression_threshold_percent: 75,
          compression_threshold_tokens: 96000,
          remaining_tokens: 64000,
          summary_tokens: 0,
          recent_message_count: 18,
          compacted_through_message_id: null,
          last_compacted_at: null,
          token_source: "estimate",
          state: "ready",
        }}
      />,
    )

    expect(screen.getByTestId("chat-context-usage")).toHaveTextContent("上下文")
    expect(screen.getByText("66.7%")).toBeInTheDocument()
    expect(screen.getByRole("progressbar", { name: "上下文 66.7%" })).toHaveAttribute(
      "aria-valuenow",
      "66.7",
    )
  })

  it("shows compaction in progress when the budget reaches 100%", () => {
    render(
      <ContextUsageIndicator
        status={{
          conversation_id: 7,
          context_window_tokens: 32768,
          estimated_tokens: 24576,
          used_percent: 75,
          compression_progress_percent: 100,
          compression_threshold_percent: 75,
          compression_threshold_tokens: 24576,
          remaining_tokens: 8192,
          summary_tokens: 0,
          recent_message_count: 20,
          compacted_through_message_id: null,
          last_compacted_at: null,
          token_source: "estimate",
          state: "compressing",
        }}
      />,
    )

    const status = screen.getByRole("status")
    expect(status).toHaveTextContent("正在压缩上下文")
    expect(status).toHaveTextContent("100.0%")
    expect(screen.getByRole("progressbar", { name: "正在压缩上下文 100%" })).toHaveAttribute(
      "aria-valuenow",
      "100",
    )
  })

  it("shows an evidence-rich compaction receipt", () => {
    render(
      <ContextCompressionBanner
        notice={{
          mode: "persistent",
          revision: 2,
          summarized_message_count: 20,
          summarized_turn_count: 10,
          duplicate_messages_omitted: 3,
          through_message_id: 42,
          before_tokens: 98000,
          before_percent: 76.6,
          after_tokens: 43000,
          after_percent: 33.6,
          summary_tokens: 2100,
        }}
      />,
    )

    const receipt = screen.getByTestId("chat-context-compressed")
    expect(receipt).toHaveTextContent("已压缩早期对话 10 轮")
    expect(receipt).toHaveTextContent("76.6% → 33.6%")
    expect(receipt).toHaveTextContent("去除重复 3")
  })
})
