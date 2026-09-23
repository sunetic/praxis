import { test, expect } from "@playwright/test"
import { writeFile } from "node:fs/promises"

// Explicit opt-in; creates test artifacts and contacts the configured LLM.
test("real Function conversation edits, shows actual checks, and restores the same history", async ({ page }, info) => {
  const base = process.env.PRAXIS_BROWSER_LIVE_URL
  test.skip(!base, "Provide an isolated configured product server")
  test.setTimeout(300_000)
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  const created = await page.request.post(`${base}/api/v1/functions`, { data: { name: "浏览器订单汇总自测" } })
  expect(created.status()).toBe(201)
  const fn = await created.json()
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  const reports: Record<string, unknown>[] = []
  let conversationId: string | undefined
  let proposal: unknown
  const prompts = [
    "请编写订单金额汇总 Function：输入 payload.orders 是对象数组，每项有 amount，返回 count 和 total。缺少 orders 或金额不是数字时抛出 ValueError。保存草稿并执行可用检查，不要发布，不要查询数据库。请用简短中文说明结果。",
    "补充：空数组返回 count=0、total=0；金额为负数时抛出 ValueError，保留前面的要求。修改后再检查，仍不要发布。",
    "只用两句话告诉我哪些检查实际执行了、哪些没有执行，不要修改代码，也不要再调用工具。",
  ]
  try {
    await page.goto(`${base}/function/${fn.id}/build`)
    await expect(page.getByRole("heading", { name: fn.name })).toBeVisible()
    await expect(page.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    for (let index = 0; index < prompts.length; index++) {
      const report: Record<string, unknown> = { prompt: prompts[index] }; reports.push(report)
      await page.getByRole("textbox", { name: "消息", exact: true }).fill(prompts[index])
      const accepted = page.waitForResponse(response => /\/conversations\/[^/]+\/runs$/.test(response.url()) && response.request().method() === "POST")
      const started = performance.now()
      await page.getByRole("button", { name: "发送", exact: true }).click()
      await expect(page.getByText(prompts[index], { exact: true })).toBeVisible()
      report.observedFeedbackMs = performance.now() - started
      const response = await accepted
      expect(response.status()).toBe(202)
      const run = await response.json(); conversationId = run.conversation_id
      let state: Record<string, unknown> = {}
      await expect.poll(async () => {
        const result = await page.request.get(`${base}/api/v1/runs/${run.id}`)
        state = await result.json(); report.state = state
        return state.status
      }, { timeout: 100_000, intervals: [250, 500, 1000] }).toBe("finished")
      const artifact = await (await page.request.get(`${base}/api/v1/functions/${fn.id}/draft`)).json()
      const events = (await (await page.request.get(`${base}/api/v1/runs/${run.id}/events`)).text()).split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)))
      Object.assign(report, { artifact, events, seconds: (performance.now() - started) / 1000 })
      expect(artifact.revision_id).toBeTruthy()
      expect(artifact.current_release_id).toBeNull()
      expect(artifact.validation).toBeTruthy()
      const article = page.locator(`[data-run-id="${run.id}"]`)
      await expect(article.locator("[data-message-id]").first()).toBeVisible()
      await expect(article.getByRole("button", { name: "停止", exact: true })).toHaveCount(0)
      const visible = await article.innerText(); report.transcript = visible
      await expect(page.getByTestId("artifact-state")).toContainText(artifact.revision_id)
      if (artifact.validation.checks.some((check: { executed: boolean; status: string }) => !check.executed || check.status !== "passed")) {
        await expect(page.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
      }
      if (index === 1) expect(artifact.revision_id).not.toBe((reports[0].artifact as { revision_id: string }).revision_id)
      if (index === 2) {
        expect(state.tool_calls).toEqual([])
        expect(artifact.revision_id).toBe((reports[1].artifact as { revision_id: string }).revision_id)
      }
      await page.reload()
      await expect.poll(() => page.locator(`[data-run-id="${run.id}"]`).innerText()).toBe(visible)
      await expect(page.locator("[data-run-id]")).toHaveCount(index + 1)
    }
    // Same model factory, one direct structured request, with no execution or publication.
    const suggestion = await page.request.post(`${base}/api/v1/functions/${fn.id}/suggest-input`, { data: { runtime_path: "draft", prompt: "为当前订单汇总代码提供一个包含两项正数金额的简单示例输入。简短说明，这只是示例，没有执行。" } })
    proposal = { status: suggestion.status(), body: await suggestion.json() }
    expect(suggestion.status()).toBe(200)
    expect(errors).toEqual([])
    await page.screenshot({ path: info.outputPath("function-desktop.png"), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(page.getByRole("textbox", { name: "消息", exact: true })).toBeVisible()
    await page.getByRole("complementary", { name: "当前草稿" }).scrollIntoViewIfNeeded()
    await expect(page.getByText("版本检查", { exact: true })).toBeVisible()
    expect(await page.evaluate("document.documentElement.scrollWidth <= innerWidth")).toBe(true)
    await page.screenshot({ path: info.outputPath("function-mobile.png"), fullPage: true })
  } finally {
    const path = info.outputPath("real-function-transcripts.json")
    await writeFile(path, JSON.stringify({ functionId: fn.id, conversationId, reports, proposal, browserErrors: errors, acceptanceComplete: false, measurementNote: "Playwright observation, not calibrated latency or p95" }, null, 2))
    await info.attach("real-function-transcripts", { path, contentType: "application/json" })
  }
})
