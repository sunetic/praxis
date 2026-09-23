import { test, expect } from "@playwright/test"
import type { FunctionDraft } from "../src/lib/functionArtifacts"
import { replayServer } from "./replay-server"

test("Function artifact updates before run completion using only the real call-ID event relationship", async ({ page }, info) => {
  let draft: FunctionDraft = { function_id: 1, name: "产物事件回放", slug: "artifact", code: "", dependencies: {}, revision_id: null, revision_hash: "a".repeat(64), current_release_id: null, released_revision_id: null, changed_files: [], validation: null }
  const fixture = await replayServer(draft)
  try {
    await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
    await page.goto(`${fixture.url}/function/1/build`)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("产物事件回放请求")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect.poll(() => fixture.cursors.length).toBe(1)
    fixture.send("tool_start", { call_id: "write", name: "function_write" })
    draft = { ...draft, revision_id: "saved-during-run", code: "def main(payload, context): return payload", changed_files: ["main.py"] }
    fixture.setDraft(draft)
    fixture.send("tool_result", { call_id: "write", status: "succeeded", outcome: "success", content: { revision_id: draft.revision_id } })
    await expect(page.getByTestId("artifact-state")).toContainText("saved-during-run")
    await expect(page.getByRole("button", { name: "停止", exact: true })).toBeVisible()
    fixture.send("tool_start", { call_id: "check", name: "function_validate" })
    draft = { ...draft, validation: { id: "checked-during-run", revision_id: draft.revision_id!, revision_hash: draft.revision_hash, created_at: 1, checks: [
      { name: "python_syntax", executed: true, status: "passed" }, { name: "entrypoint", executed: true, status: "passed" }, { name: "controlled_runtime", executed: false, status: "unavailable" },
    ] } }
    fixture.setDraft(draft)
    fixture.send("tool_result", { call_id: "check", status: "succeeded", outcome: "success", content: draft.validation })
    await expect(page.getByText("环境不可用 · 未执行", { exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    fixture.send("assistant_delta", { message_id: "m", part_id: "0", text: "已完成" })
    fixture.send("run_finished", { status: "finished" })
    await expect(page.getByText("已完成", { exact: true })).toHaveCount(1)
    await page.reload()
    await expect(page.getByText("已完成", { exact: true })).toHaveCount(1)
    await expect(page.getByText("环境不可用 · 未执行", { exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    expect(fixture.submissions).toHaveLength(1)
    await page.screenshot({ path: info.outputPath("function-artifact-replay.png"), fullPage: true })
  } finally { await fixture.close() }
})
