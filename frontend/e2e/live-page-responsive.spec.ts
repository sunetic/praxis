import { test, expect } from "@playwright/test"
import { writeFile } from "node:fs/promises"

// Corrective continuation of an existing, model-authored Page smoke specimen.
// Explicit opt-in: this submits a real user correction, not fixture source code.
test("real Page conversation repairs narrow-preview overflow without losing filters", async ({ page }, info) => {
  const base = process.env.PRAXIS_LIVE_URL
  const pageId = Number(process.env.PRAXIS_LIVE_PAGE_ID)
  test.skip(!base || !Number.isSafeInteger(pageId) || pageId <= 0, "Requires an isolated real-model service and a Page smoke specimen")
  test.setTimeout(240_000)
  const evidence: Record<string, unknown> = { pageId, acceptanceComplete: false }
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  await page.addInitScript(() => {
    if (window === window.top) localStorage.setItem("praxis.locale", "zh-CN")
  })
  await page.setViewportSize({ width: 390, height: 844 })
  const health = await (await page.request.get(`${base}/api/v1/schedules/worker-health`)).json()
  expect(health.autostart).toBe(false)
  const before = await (await page.request.get(`${base}/api/v1/pages/${pageId}/draft`)).json()
  evidence.before = before
  try {
    await page.goto(`${base}/page/workspace/${pageId}`)
    const preview = page.frameLocator('iframe[title="编译产物预览"]')
    await expect(preview.getByLabel("最低金额", { exact: false })).toBeAttached()
    const geometry = () => preview.locator("html").evaluate(element => ({ width: element.scrollWidth, viewport: innerWidth }))
    evidence.beforeGeometry = await geometry()
    await page.screenshot({ path: info.outputPath("before.png"), fullPage: true })
    const prompt = "实际在手机宽度下查看，预览里的搜索和最低金额输入框横向溢出了，后一个输入框落在可视范围外。请修好窄屏布局：在约 320px 的页面内容宽度下不需要横向滚动，两个输入框和订单表格都能正常看见和操作；保留已有搜索、最低金额联合筛选、空结果提示和合计功能。保存并执行可用检查，不发布，不创建 Function，不查询数据库。用简短中文说明改动和未验证项。"
    evidence.prompt = prompt
    const accepted = page.waitForResponse(response => /\/conversations\/[^/]+\/runs$/.test(response.url()) && response.request().method() === "POST")
    await page.getByRole("textbox", { name: "消息", exact: true }).fill(prompt)
    const started = performance.now()
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const response = await accepted
    expect(response.status()).toBe(202)
    const run = await response.json()
    let state: Record<string, unknown> = {}
    await expect.poll(async () => {
      state = await (await page.request.get(`${base}/api/v1/runs/${run.id}`)).json()
      evidence.state = state
      return state.status
    }, { timeout: 180_000, intervals: [250, 500, 1000] }).toBe("finished")
    evidence.seconds = (performance.now() - started) / 1000
    const draft = await (await page.request.get(`${base}/api/v1/pages/${pageId}/draft`)).json()
    evidence.draft = draft
    evidence.events = (await (await page.request.get(`${base}/api/v1/runs/${run.id}/events`)).text()).split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)))
    expect(draft.revision_id).not.toBe(before.revision_id)
    expect(draft.current_release_id).toBeNull()
    expect(draft.artifact_hash).toBeTruthy()
    await expect(page.getByTestId("artifact-state")).toContainText(draft.revision_id)
    await expect(page.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    await expect(page.locator(`[data-run-id="${run.id}"]`).getByRole("button", { name: "停止", exact: true })).toHaveCount(0)
    evidence.transcript = await page.locator(`[data-run-id="${run.id}"]`).innerText()
    const search = preview.getByLabel("订单号搜索", { exact: false })
    const minimum = preview.getByLabel("最低金额", { exact: false })
    await expect(search).toBeVisible()
    await expect(minimum).toBeVisible()
    evidence.afterGeometry = await geometry()
    expect(await preview.locator("html").evaluate(element => element.scrollWidth <= innerWidth)).toBe(true)
    for (const input of [search, minimum]) {
      expect(await input.evaluate(element => {
        const rect = element.getBoundingClientRect()
        return rect.left >= 0 && rect.right <= innerWidth
      })).toBe(true)
    }
    const expectTotal = (amount: number) => expect(preview.locator("body *").filter({ hasText: new RegExp(`(?:订单)?合计\\s*[:：]?\\s*[¥￥]?\\s*${amount}(?:\\.0{1,2})?\\s*(?:元)?$`) }).last()).toBeVisible()
    await expectTotal(600)
    await minimum.fill("200")
    await expect(preview.getByText("A001", { exact: true })).toHaveCount(0)
    await expectTotal(500)
    await search.fill("A003")
    await expect(preview.getByText("A002", { exact: true })).toHaveCount(0)
    await expect(preview.getByText("A003", { exact: true })).toBeVisible()
    await expectTotal(300)
    await minimum.fill("400")
    await expect(preview.getByText("没有匹配订单", { exact: true })).toBeVisible()
    await expectTotal(0)
    expect(errors).toEqual([])
    await page.locator("iframe").screenshot({ path: info.outputPath("mobile-preview.png") })
    await page.screenshot({ path: info.outputPath("after.png"), fullPage: true })
    evidence.interactionsPassed = true
  } finally {
    evidence.errors = errors
    const path = info.outputPath("real-page-responsive.json")
    await writeFile(path, JSON.stringify(evidence, null, 2))
    await info.attach("real-page-responsive", { path, contentType: "application/json" })
  }
})
