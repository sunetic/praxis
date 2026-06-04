import { describe, expect, it } from "vitest"

import { stripSuggestedFlags } from "./SettingsPage"

describe("stripSuggestedFlags", () => {
  it("removes auto-discovered flags from suggested command", () => {
    const command = "claude -p --output-format json --permission-mode bypassPermissions --allowedTools Edit Read Write Bash"
    const flags = [
      "-p",
      "--output-format json",
      "--permission-mode bypassPermissions",
      "--allowedTools Edit Read Write Bash",
    ]

    expect(stripSuggestedFlags(command, flags)).toBe("claude")
  })

  it("keeps the original command when no flags match", () => {
    expect(stripSuggestedFlags("cursor --cli", ["-p"])).toBe("cursor --cli")
  })
})
