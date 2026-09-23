import { test, expect } from "@playwright/test"
import { writeFile } from "node:fs/promises"

// Explicit opt-in: this contacts the configured model and an authorized test DB.
// Start the isolated product server yourself; never point this at production data.
test("real model chat renders, follows up, queries read-only data, and restores the same history", async ({ page }, info) => {
  const base = process.env.PRAXIS_BROWSER_LIVE_URL
  test.skip(!base, "Set PRAXIS_BROWSER_LIVE_URL to an isolated, configured product server")
  test.setTimeout(240_000)
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  const datasourceResponse = await page.request.get(`${base}/api/v1/datasources`)
  expect(datasourceResponse.ok()).toBe(true)
  const datasources = await datasourceResponse.json()
  expect(datasources.length).toBeGreaterThan(0)
  const created = await page.request.post(`${base}/api/v1/conversations`, {
    data: {
      title: "Browser native runtime smoke",
      scene: { datasource_ids: [datasources[0].id] },
    },
  })
  expect(created.status()).toBe(201)
  const conversation = await created.json()
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  const turns: unknown[] = []
  await page.goto(`${base}/chat?conversationId=${conversation.id}`)
  const prompts = [
    "用中文简要说明数据库索引适合解决什么问题、有什么代价，各一点。不要查询数据库。",
    "只展开刚才的代价，用两句话解释，不要重复第一点。",
    "在已授权的数据源里执行只读查询 SELECT 13 AS browser_probe，简短告诉我实际返回值，不修改任何数据。",
  ]
  try {
  for (let index = 0; index < prompts.length; index++) {
    const prompt = prompts[index]
    const turn: Record<string, unknown> = { prompt }
    turns.push(turn)
    await page.getByRole("textbox", { name: "消息", exact: true }).fill(prompt)
    const submitted = page.waitForResponse(response => response.url().endsWith(`/conversations/${conversation.id}/runs`) && response.request().method() === "POST")
    const started = performance.now()
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect(page.getByText(prompt, { exact: true })).toBeVisible()
    const observedFeedbackMs = performance.now() - started
    const response = await submitted
    expect(response.status()).toBe(202)
    const run = await response.json()
    let state: Record<string, unknown> = {}
    await expect.poll(async () => {
      const result = await page.request.get(`${base}/api/v1/runs/${run.id}`)
      state = await result.json()
      return state.status
    }, { timeout: 90_000, intervals: [200, 500, 1000] }).toBe("finished")
    const article = page.locator(`[data-run-id="${run.id}"]`)
    await expect(article.locator("[data-message-id]").first()).toBeVisible()
    await expect(article.getByRole("button", { name: "停止", exact: true })).toHaveCount(0)
    const transcript = await article.innerText()
    const trace = await page.request.get(`${base}/api/v1/runs/${run.id}/events`)
    const events = (await trace.text()).split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)))
    expect(events.map(event => event.seq)).toEqual(Array.from({ length: events.length }, (_, i) => i + 1))
    const calls = state.tool_calls as { name: string; status: string; result: { content: { rows?: unknown[] } } }[]
    if (index < 2) expect(calls).toHaveLength(0)
    else expect(calls.some(call => call.name === "query_database" && call.status === "succeeded" && JSON.stringify(call.result.content.rows) === JSON.stringify([{ browser_probe: 13 }]))).toBe(true)
    Object.assign(turn, { state, events, transcript, observedFeedbackMs, measurementNote: "Playwright round-trip observation, not calibrated browser feedback or p95" })
    await page.reload()
    await expect(page.locator(`[data-run-id="${run.id}"] [data-message-id]`).first()).toBeVisible()
    await expect.poll(() => page.locator(`[data-run-id="${run.id}"]`).innerText()).toBe(transcript)
    const history = await page.request.get(`${base}/api/v1/conversations/${conversation.id}/runs`)
    expect((await history.json()).length).toBe(index + 1)
  }
  expect(errors).toEqual([])
  } finally {
    const reportPath = info.outputPath("real-chat-transcripts.json")
    await writeFile(reportPath, JSON.stringify({ kind: "browser-live-smoke", acceptanceComplete: false, turns, browserErrors: errors }, null, 2))
    await info.attach("real-chat-transcripts", { path: reportPath, contentType: "application/json" })
    await page.screenshot({ path: info.outputPath("real-chat.png"), fullPage: true }).catch(() => undefined)
  }
})
