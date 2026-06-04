import { execSync } from "node:child_process"
import { resolve } from "node:path"
import { describe, it, expect } from "vitest"

describe("TypeScript", () => {
  it("project passes tsc --noEmit with no type errors", () => {
    const root = resolve(__dirname, "../..")
    const tsc = resolve(root, "node_modules/.bin/tsc")
    try {
      execSync(`${tsc} --noEmit`, { cwd: root, timeout: 60_000, stdio: "pipe" })
    } catch (err) {
      const stdout = (err as { stdout?: Buffer }).stdout?.toString() ?? ""
      expect.fail(`tsc --noEmit failed:\n${stdout}`)
    }
  })
})
