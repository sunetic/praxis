import { describe, expect, it } from "vitest"

import { normalizeRuntimeStreamEvent } from "@/lib/runtimeStream"
import { buildContentParts } from "./ChatThreadView"

describe("chat progress narration", () => {
  it("normalizes user-visible progress as an extension event", () => {
    const event = normalizeRuntimeStreamEvent({
      type: "assistant_progress",
      data: {
        text: "我先确认实际表结构，再开始查询。",
        stage: "planning",
      },
    })

    expect(event).toMatchObject({
      kind: "extension",
      name: "assistant_progress",
      summary: "我先确认实际表结构，再开始查询。",
    })
  })

  it("renders persisted progress as normal dialogue between tool calls", () => {
    const parts = buildContentParts({
      content_parts: [
        { type: "progress", text: "先确认表结构。", stage: "planning" },
        { type: "tool_use", id: "tc-1", name: "execute_sql", input: { sql: "SHOW TABLES" } },
        { type: "progress", text: "结构已确认，继续核对金额。", stage: "reflecting" },
      ],
    })

    expect(parts).toEqual([
      { type: "text", text: "先确认表结构。" },
      {
        type: "tool-call",
        toolCallId: "tc-1",
        toolName: "execute_sql",
        args: { sql: "SHOW TABLES" },
        argsText: '{\n  "sql": "SHOW TABLES"\n}',
        result: undefined,
      },
      { type: "text", text: "结构已确认，继续核对金额。" },
    ])
  })
})
