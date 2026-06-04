import { describe, expect, it } from "vitest"

import {
  buildAttemptTimelineEntries,
  buildConversationContext,
  buildPhaseTimelineEntries,
  formatBuildWaitingText,
} from "./buildChatRuntime"

describe("buildChatRuntime", () => {
  it("buildConversationContext skips status messages and keeps latest turns", () => {
    const context = buildConversationContext(
      [
        { role: "assistant", content: "A1" },
        { role: "status", content: "S1" },
        { role: "user", content: "U1" },
      ],
      "U2",
      3
    )
    expect(context).toBe("assistant: A1\nuser: U1\nuser: U2")
  })

  it("buildPhaseTimelineEntries maps and deduplicates phase messages", () => {
    const entries = buildPhaseTimelineEntries({
      events: [
        { phase: "intent_parsed", summary: "Plan done", payload: { phase_duration_ms: 88 } },
        { phase: "intent_parsed", summary: "Plan done", payload: { phase_duration_ms: 88 } },
        { phase: "apply", summary: "Apply done", payload: { phase_duration_ms: 120 } },
      ],
      idPrefix: "x",
      allowedPhases: new Set(["intent_parsed", "apply"]),
      phaseLabelMap: { intent_parsed: "Plan", apply: "Apply" },
      assistantText: "Apply done",
    })
    expect(entries).toHaveLength(2)
    expect(entries[0].message.content).toContain("Plan done")
  })

  it("buildAttemptTimelineEntries renders observe/reflect/retry sequence", () => {
    const entries = buildAttemptTimelineEntries({
      events: [
        {
          phase: "apply",
          payload: {
            attempts: [
              { attempt: 1, status: "failed", summary: "verification_failed", diagnostics: ["missing field"] },
              { attempt: 2, status: "done", summary: "verification_passed", diagnostics: [] },
            ],
          },
        },
      ],
      idPrefix: "attempt",
    })
    const content = entries.map((item) => item.message.content).join(" | ")
    expect(content).toContain("Observe · Attempt 1 失败")
    expect(content).toContain("Reflect · missing field")
    expect(content).toContain("Retry · 发起 Attempt 2")
    expect(content).toContain("Act · Attempt 2 成功")
  })

  it("formatBuildWaitingText picks stage by elapsed time and appends seconds", () => {
    const stages = [
      { atMs: 0, label: "Plan · 需求解析" },
      { atMs: 5000, label: "Act · 草稿构建" },
      { atMs: 12000, label: "Observe · 校验结果" },
    ]
    expect(formatBuildWaitingText(0, stages, "处理中")).toBe("Plan · 需求解析")
    expect(formatBuildWaitingText(6400, stages, "处理中")).toBe("Act · 草稿构建 · 6s")
    expect(formatBuildWaitingText(13000, stages, "处理中")).toBe("Observe · 校验结果 · 13s")
  })

  it("formatBuildWaitingText falls back when stage list is empty", () => {
    expect(formatBuildWaitingText(3600, [], "处理中")).toBe("处理中 · 3s")
  })
})
