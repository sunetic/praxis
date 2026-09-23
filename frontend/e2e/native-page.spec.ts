import { test, expect } from "@playwright/test"
import type { PageDraft } from "../src/lib/pageArtifacts"
import { replayServer } from "./replay-server"

test("Page artifact and opaque preview follow call-ID facts before the model finishes", async ({ page }, info) => {
  let draft: PageDraft = { page_id: 1, name: "Page 事件回放", files: {}, bindings: {}, revision_id: null, revision_hash: "a".repeat(64), current_release_id: null, released_revision_id: null, changed_files: [], bindings_changed: false, artifact_hash: null, validation: null, owned_functions: [] }
  const fixture = await replayServer(undefined, draft)
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  try {
    await page.addInitScript(() => {
      if (window === window.top) localStorage.setItem("praxis.locale", "zh-CN")
    })
    await page.goto(`${fixture.url}/page/workspace/1`)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("Page 产物回放")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect.poll(() => fixture.cursors.length).toBe(1)
    fixture.send("tool_start", { call_id: "write", name: "page_write" })
    draft = { ...draft, revision_id: "source-1", files: { "main.tsx": "source fixture" }, changed_files: ["main.tsx"] }
    fixture.setPageDraft(draft)
    fixture.send("tool_result", { call_id: "write", status: "succeeded", outcome: "success", content: { revision_id: "source-1" } })
    await expect(page.getByTestId("artifact-state")).toContainText("source-1")
    await expect(page.getByRole("button", { name: "停止", exact: true })).toBeVisible()
    fixture.send("tool_start", { call_id: "check", name: "page_validate" })
    draft = { ...draft, artifact_hash: "compiled-1", validation: { id: "check-1", revision_id: "source-1", revision_hash: draft.revision_hash, checks: [
      { name: "source_compile", status: "passed", executed: true }, { name: "function_bindings", status: "passed", executed: true },
      { name: "browser_runtime", status: "unavailable", executed: false }, { name: "binding_runtime", status: "not_run", executed: false, applicable: false },
    ] } }
    fixture.setPageDraft(draft, `<main><p id="result">Preview loaded</p><script>
      try { parent.document.body.dataset.escaped = "yes" } catch { document.body.dataset.isolated = "yes" }
      addEventListener("message", event => { if (event.data.type === "praxis.page.result") document.getElementById("result").textContent = event.data.error; });
      parent.postMessage({type:"praxis.page.invoke",id:"test-call",name:"unauthorized",payload:{}}, "*");
    </script></main>`)
    fixture.send("tool_result", { call_id: "check", status: "succeeded", outcome: "success", content: draft.validation })
    await expect(page.getByText("环境不可用 · 未执行", { exact: true })).toBeVisible()
    const frame = page.frameLocator('iframe[title="编译产物预览"]')
    await expect(frame.locator("#result")).toContainText("Function 调用桥接尚不可用")
    expect(await page.evaluate("document.body.dataset.escaped")).toBeUndefined()
    await expect(frame.locator("body")).toHaveAttribute("data-isolated", "yes")
    await expect(page.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    fixture.send("tool_start", { call_id: "edit", name: "page_edit" })
    draft = { ...draft, revision_id: "source-2", revision_hash: "b".repeat(64), validation: null, artifact_hash: null }
    fixture.setPageDraft(draft)
    fixture.send("tool_result", { call_id: "edit", status: "succeeded", outcome: "success", content: { revision_id: "source-2" } })
    await expect(page.getByTestId("artifact-state")).toContainText("source-2")
    await expect(page.locator("iframe")).toHaveCount(0)
    fixture.send("assistant_delta", { message_id: "m", part_id: "0", text: "草稿已保存，未发布。" })
    fixture.send("run_finished", { status: "finished" })
    await page.reload()
    await expect(page.getByText("草稿已保存，未发布。", { exact: true })).toHaveCount(1)
    expect(fixture.submissions).toHaveLength(1)
    expect(errors).toEqual([])
    await page.screenshot({ path: info.outputPath("page-facts.png"), fullPage: true })
  } finally { await fixture.close() }
})
