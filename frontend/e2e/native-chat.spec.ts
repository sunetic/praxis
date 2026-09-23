import { test, expect } from "@playwright/test"
import { replayServer } from "./replay-server"

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
})

test("conversation auto-approval requires confirmation and is revoked by a scope change", async ({ page }) => {
  const fixture = await replayServer(undefined, undefined, { datasource_ids: [1] })
  try {
    page.on("dialog", dialog => dialog.accept())
    await page.goto(`${fixture.url}/chat`)
    const toggle = page.getByRole("button", { name: "自动批准：关" })
    await expect(toggle).toBeEnabled()
    const before = Date.now() / 1000
    await toggle.click()
    await expect(page.getByRole("button", { name: "自动批准：30 分钟" })).toBeVisible()
    expect(fixture.scene.auto_approval).toMatchObject({
      tool_name: "request_database_change",
      agent_id: null,
      datasource_id: 1,
    })
    expect(fixture.scene.auto_approval!.expires_at).toBeGreaterThanOrEqual(before + 29 * 60)
    await page.getByRole("combobox", { name: "数据源范围" }).selectOption("none")
    await expect(page.getByRole("button", { name: "自动批准：关" })).toBeDisabled()
    expect(fixture.scene.auto_approval).toBeNull()
    await page.getByRole("combobox", { name: "数据源范围" }).selectOption("1")
    await expect(page.getByRole("button", { name: "自动批准：关" })).toBeEnabled()
    expect(fixture.scene.auto_approval).toBeNull()
  } finally { await fixture.close() }
})

test("context compression is a factual status and capacity limits preserve the transcript", async ({ page }, info) => {
  const fixture = await replayServer()
  try {
    await page.goto(`${fixture.url}/chat`)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("上下文状态回放")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect.poll(() => fixture.cursors.length).toBe(1)
    fixture.send("context_compaction_started", { created_at: Date.now() / 1000 - 12 })
    await expect(page.getByRole("status").filter({ hasText: "正在整理上下文" })).toContainText(/1[2-9]s/)
    await expect(page.locator("[data-message-id]")).toHaveCount(0)
    await expect(page.getByRole("button", { name: "停止", exact: true })).toBeEnabled()
    fixture.send("run_limited", { status: "limited", error_code: "context_limit" })
    await expect(page.getByRole("status")).toContainText("原始记录仍在")
    await expect(page.getByText("上下文状态回放", { exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "停止", exact: true })).toHaveCount(0)
    await page.screenshot({ path: info.outputPath("context-limit.png"), fullPage: true })
  } finally { await fixture.close() }
})

test("incremental native text and tools survive disconnect and refresh without resubmission", async ({ page }, info) => {
  const fixture = await replayServer()
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  try {
    fixture.hold()
    await page.goto(`${fixture.url}/chat`)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("浏览器回放请求")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect(page.getByText("浏览器回放请求", { exact: true })).toBeVisible()
    await expect(page.getByText("正在提交…", { exact: true })).toBeVisible()
    await expect.poll(() => fixture.submissions.length).toBe(1)
    fixture.acknowledge()
    await expect.poll(() => fixture.cursors.length).toBe(1)
    fixture.send("request_started")
    fixture.send("assistant_delta", { message_id: "first", part_id: "0", text: "查询说明\n\n```sql\nSELECT" })
    await expect(page.locator('[data-message-id="first"]')).toContainText("SELECT")
    fixture.send("assistant_delta", { message_id: "first", part_id: "0", text: " 7;\n```" })
    fixture.send("assistant_message_end", { message_id: "first" })
    fixture.send("tool_start", { call_id: "query", name: "query_database", arguments: { sql: "SELECT 7" } })
    fixture.send("tool_result", { call_id: "query", status: "succeeded", content: { value: 7 } })
    await expect(page.locator('[data-tool-id="query"]')).toContainText("已执行")
    const beforeDisconnect = fixture.events.length
    fixture.disconnect()
    fixture.send("request_started")
    fixture.send("assistant_delta", { message_id: "last", part_id: "0", text: "实际返回 7。" })
    fixture.send("assistant_message_end", { message_id: "last" })
    fixture.send("run_finished", { status: "finished" })
    await expect(page.getByText("实际返回 7。", { exact: true })).toHaveCount(1)
    expect(fixture.cursors).toContain(beforeDisconnect)
    await page.reload()
    await expect(page.getByText("实际返回 7。", { exact: true })).toHaveCount(1)
    await expect(page.locator('[data-message-id="first"]')).toContainText("SELECT 7;")
    expect(fixture.submissions).toHaveLength(1)
    expect(fixture.cancellations).toBe(0)
    expect(errors).toEqual([])
    await info.attach("native-events", { body: JSON.stringify(fixture.events, null, 2), contentType: "application/json" })
    await page.screenshot({ path: info.outputPath("native-chat.png"), fullPage: true })
  } finally { await fixture.close() }
})

test("multiple approval decisions do not imply execution, and unknown effects stay visible", async ({ page }, info) => {
  const fixture = await replayServer()
  try {
    await page.goto(`${fixture.url}/chat`)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("审批回放请求")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect.poll(() => fixture.cursors.length).toBe(1)
    for (const call of ["one", "two"]) fixture.send("approval_required", { call_id: call, name: "save_draft", fingerprint: call.repeat(64).slice(0, 64), target: { path: `${call}.py`, revision: 1 }, arguments: { content: "sample" } })
    fixture.send("run_paused")
    const one = page.locator('[data-tool-id="one"]')
    const two = page.locator('[data-tool-id="two"]')
    await one.getByRole("button", { name: "批准此操作" }).click()
    await expect(one).toContainText("决定已提交")
    await expect(one).not.toContainText("已执行")
    await two.getByRole("button", { name: "拒绝此操作" }).click()
    expect(fixture.decisions.map(item => item.approved)).toEqual([true, false])
    fixture.send("tool_result", { call_id: "two", status: "denied", content: "Denied by user" })
    fixture.send("tool_start", { call_id: "one" })
    fixture.send("tool_result", { call_id: "one", status: "outcome_unknown", content: "External result not confirmed" })
    fixture.send("run_failed", { status: "interrupted", error_code: "outcome_unknown" })
    await expect(one).toContainText("结果未知")
    await expect(two).toContainText("未获批准")
    await expect(page.locator('[data-tool-id="one"]')).toHaveCount(1)
    await info.attach("native-events", { body: JSON.stringify(fixture.events, null, 2), contentType: "application/json" })
  } finally { await fixture.close() }
})

test("long output leaves an upward-scrolling reader in place and distinguishes cancel from modify", async ({ page }) => {
  const fixture = await replayServer()
  try {
    await page.goto(`${fixture.url}/chat`)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("长文本回放")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect.poll(() => fixture.cursors.length).toBe(1)
    fixture.send("request_started")
    fixture.send("assistant_delta", { message_id: "long", part_id: "0", text: Array.from({ length: 80 }, (_, i) => `第 ${i + 1} 段内容。\n\n`).join("") })
    const viewport = page.getByTestId("run-viewport")
    await expect(page.locator('[data-message-id="long"]')).toContainText("第 80 段内容")
    await viewport.evaluate(element => { element.scrollTop = 100; element.dispatchEvent(new Event("scroll")) })
    fixture.send("assistant_delta", { message_id: "long", part_id: "0", text: "尾部新增内容。" })
    await expect(page.getByRole("button", { name: "回到底部" })).toBeVisible()
    expect(await viewport.evaluate(element => element.scrollTop)).toBe(100)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("新的要求")
    await expect(page.getByRole("button", { name: "排队发送" })).toBeVisible()
    await page.getByRole("button", { name: "停止并修改" }).click()
    await expect.poll(() => fixture.submissions.length).toBe(2)
    expect(fixture.submissions[1].mode).toBe("stop_and_modify")
    expect(fixture.cancellations).toBe(0)
  } finally { await fixture.close() }
})

test("a long provider wait shows elapsed time and explicit cancellation, not assistant progress", async ({ page }) => {
  const fixture = await replayServer()
  try {
    await page.goto(`${fixture.url}/chat`)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("等待状态回放")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect.poll(() => fixture.cursors.length).toBe(1)
    fixture.send("request_started", { created_at: Date.now() / 1000 - 12 })
    await expect(page.getByRole("status").filter({ hasText: /等待模型响应/ })).toContainText(/1[2-9]s/)
    await expect(page.locator("[data-message-id]")).toHaveCount(0)
    await page.getByRole("button", { name: "停止", exact: true }).click()
    await expect.poll(() => fixture.cancellations).toBe(1)
    await expect(page.getByText(/正在停止；已执行的操作不会回滚/)).toBeVisible()
    fixture.send("run_cancelled", { status: "cancelled" })
    await expect(page.getByText("已停止", { exact: true })).toBeVisible()
    expect(fixture.submissions).toHaveLength(1)
  } finally { await fixture.close() }
})
