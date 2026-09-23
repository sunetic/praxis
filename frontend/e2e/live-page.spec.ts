import { test, expect } from "@playwright/test"
import { writeFile } from "node:fs/promises"

test("real Page conversation saves, compiles, modifies, previews and restores one history", async ({ page }, info) => {
  const base = process.env.PRAXIS_LIVE_URL
  test.skip(!base, "Requires an isolated service with a real model")
  test.setTimeout(480_000)
  const reports: Record<string, unknown>[] = []
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  // Init scripts also run in srcDoc frames. Locale belongs to the host only;
  // accessing storage in an opaque preview is correctly forbidden.
  await page.addInitScript(() => {
    if (window === window.top) localStorage.setItem("praxis.locale", "zh-CN")
  })
  const health = await (await page.request.get(`${base}/api/v1/schedules/worker-health`)).json()
  expect(health.autostart).toBe(false)
  expect((await (await page.request.get(`${base}/api/v1/onboarding/status`)).json()).completed).toBe(true)
  const response = await page.request.post(`${base}/api/v1/pages`, { data: { name: "浏览器真实 Page 自测" } })
  expect(response.status()).toBe(201)
  const record = await response.json()
  const prompts = [
    "做一个订单列表演示页面：三条固定示例订单 A001 金额100、A002 金额200、A003 金额300，明确标注为示例数据；提供带‘订单号搜索’标签的文本输入框和可见订单合计。使用 main.tsx 与独立 CSS 文件，界面为中文。保存草稿并执行可用检查，不发布、不创建 Function、不查询数据库。用简短中文说明结果和未验证项。",
    "保留已有功能，增加带‘最低金额’标签的数字输入框，与订单号搜索同时生效。没有匹配时显示‘没有匹配订单’，合计为0。保存并检查，不发布。",
    "两句话内说明哪些检查实际执行、哪些没有；不要修改代码或调用工具。",
  ]
  try {
    await page.goto(`${base}/page/workspace/${record.id}`)
    await expect(page.getByRole("heading", { name: record.name })).toBeVisible()
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
      const run = await response.json()
      let state: Record<string, unknown> = {}
      await expect.poll(async () => {
        state = await (await page.request.get(`${base}/api/v1/runs/${run.id}`)).json(); report.state = state
        return state.status
      }, { timeout: 180_000, intervals: [250, 500, 1000] }).toBe("finished")
      const draft = await (await page.request.get(`${base}/api/v1/pages/${record.id}/draft`)).json()
      const events = (await (await page.request.get(`${base}/api/v1/runs/${run.id}/events`)).text()).split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)))
      Object.assign(report, { draft, events, seconds: (performance.now() - started) / 1000 })
      expect(draft.revision_id).toBeTruthy()
      expect(draft.current_release_id).toBeNull()
      expect(draft.validation).toBeTruthy()
      expect(draft.artifact_hash).toBeTruthy()
      await expect(page.getByTestId("artifact-state")).toContainText(draft.revision_id)
      await expect(page.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
      const article = page.locator(`[data-run-id="${run.id}"]`)
      await expect(article.getByRole("button", { name: "停止", exact: true })).toHaveCount(0)
      const visible = await article.innerText(); report.transcript = visible
      if (index === 1) expect(draft.revision_id).not.toBe((reports[0].draft as { revision_id: string }).revision_id)
      if (index === 2) {
        expect(state.tool_calls).toEqual([])
        expect(draft.revision_id).toBe((reports[1].draft as { revision_id: string }).revision_id)
      }
      await page.reload()
      await expect.poll(() => page.locator(`[data-run-id="${run.id}"]`).innerText()).toBe(visible)
      await expect(page.locator("[data-run-id]")).toHaveCount(index + 1)
    }
    const preview = page.frameLocator('iframe[title="编译产物预览"]')
    const expectTotal = async (amount: number) => {
      // Observe the smallest rendered container holding both the semantic label
      // and its value, without requiring a particular generated DOM structure.
      const total = preview.locator("body *").filter({ hasText: new RegExp(`(?:订单)?合计\\s*[:：]?\\s*[¥￥]?\\s*${amount}(?:\\.0{1,2})?\\s*(?:元)?$`) })
      await expect(total.last()).toBeVisible()
    }
    await expect(preview.getByText("A001", { exact: true })).toBeVisible()
    await expectTotal(600)
    await preview.getByLabel("最低金额", { exact: false }).fill("200")
    await expect(preview.getByText("A001", { exact: true })).toHaveCount(0)
    await expect(preview.getByText("A002", { exact: true })).toBeVisible()
    await expectTotal(500)
    await preview.getByLabel("订单号搜索", { exact: false }).fill("A003")
    await expect(preview.getByText("A002", { exact: true })).toHaveCount(0)
    await expect(preview.getByText("A003", { exact: true })).toBeVisible()
    await expectTotal(300)
    await preview.getByLabel("最低金额", { exact: false }).fill("400")
    await expect(preview.getByText("没有匹配订单", { exact: true })).toBeVisible()
    await expect(preview.getByText("A003", { exact: true })).toHaveCount(0)
    await expectTotal(0)
    // UI sandbox behavior is separate from the server's isolated runtime check.
    expect(await page.locator("iframe").evaluate((element: HTMLIFrameElement) => element.contentDocument === null)).toBe(true)
    await page.screenshot({ path: info.outputPath("page-desktop.png"), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(page.getByRole("textbox", { name: "消息", exact: true })).toBeVisible()
    expect(await page.evaluate("document.documentElement.scrollWidth <= innerWidth")).toBe(true)
    await page.getByRole("complementary", { name: "当前草稿" }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: info.outputPath("page-mobile.png"), fullPage: true })
    expect(errors).toEqual([])
  } finally {
    const path = info.outputPath("real-page-transcripts.json")
    await writeFile(path, JSON.stringify({ pageId: record.id, reports, errors, acceptanceComplete: false,
      measurementNote: "Playwright observation, not calibrated p95. UI preview does not update server validation." }, null, 2))
    await info.attach("real-page-transcripts", { path, contentType: "application/json" })
  }
})
