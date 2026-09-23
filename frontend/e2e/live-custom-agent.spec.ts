import { test, expect } from "@playwright/test"
import { writeFile } from "node:fs/promises"

test("custom Agent configuration opens native Chat and honors datasource revocation", async ({ page }, info) => {
  const base = process.env.PRAXIS_LIVE_URL
  test.skip(!base, "Requires an isolated service with a real LLM")
  test.setTimeout(300_000)
  const reports: Record<string, unknown>[] = []
  const errors: string[] = []
  const legacyRequests: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  page.on("request", request => {
    if (/\/agents\/\d+\/run|\/messages|\/chat\/stream/.test(request.url())) legacyRequests.push(request.url())
  })
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  const health = await (await page.request.get(`${base}/api/v1/schedules/worker-health`)).json()
  expect(health.autostart).toBe(false)
  const sources = []
  const suffix = Date.now().toString().slice(-6)
  for (const label of ["甲", "乙"]) {
    // These are metadata-only scope fixtures, not proof of a database connection.
    const response = await page.request.post(`${base}/api/v1/datasources`, { data: {
      name: `范围自测${label}-${suffix}`, db_type: "postgresql", host: "127.0.0.1", port: 9,
      cluster_key: `scope-${label}-${suffix}`, user: "fixture", password: "", database: "fixture",
      attributes: { fixture: "metadata-only; no connection expected" },
    } })
    expect(response.status()).toBe(201)
    sources.push(await response.json())
  }
  let agent: { id: number; name: string } | undefined
  let conversation = ""
  try {
    await page.goto(`${base}/agents`)
    await page.getByRole("button", { name: "新建 Agent", exact: true }).first().click()
    const dialog = page.getByRole("dialog")
    await dialog.getByRole("textbox", { name: "Agent 名称", exact: true }).fill(`资源助手-${suffix}`)
    await dialog.getByRole("textbox", { name: "Prompt", exact: true }).fill("你是数据库资源助手。根据实际可用工具和证据回答，使用简洁中文。不要声称做过未执行的检查。")
    await dialog.getByRole("group", { name: "可用工具" }).getByRole("checkbox", { name: /^list_datasources / }).check()
    await dialog.getByRole("group", { name: "授权数据源" }).getByRole("checkbox", { name: sources[0].name, exact: true }).check()
    await page.screenshot({ path: info.outputPath("agent-config-desktop.png"), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(dialog.getByRole("checkbox", { name: sources[0].name, exact: true })).toBeInViewport()
    await page.screenshot({ path: info.outputPath("agent-config-mobile.png"), fullPage: true })
    await page.setViewportSize({ width: 1280, height: 720 })
    const saved = page.waitForResponse(response => response.url().endsWith("/api/v1/agents") && response.request().method() === "POST")
    await dialog.getByRole("button", { name: "保存", exact: true }).click()
    const savedResponse = await saved
    expect(savedResponse.status()).toBe(201)
    const savedAgent = await savedResponse.json(); agent = savedAgent
    expect(savedAgent.tools).toEqual(["list_datasources"])
    expect(savedAgent.datasource_ids).toEqual([sources[0].id])
    await page.getByRole("button", { name: `${savedAgent.name} · 打开对话`, exact: true }).click()
    const scopeDialog = page.getByRole("dialog")
    await expect(scopeDialog.getByRole("checkbox", { name: sources[1].name, exact: true })).toHaveCount(0)
    await scopeDialog.getByRole("checkbox", { name: sources[0].name, exact: true }).check()
    const opening = page.waitForResponse(response => response.url().endsWith("/api/v1/conversations") && response.request().method() === "POST")
    await scopeDialog.getByRole("button", { name: "打开对话（1 个数据源）", exact: true }).click()
    const opened = await opening; expect(opened.status()).toBe(201)
    conversation = (await opened.json()).id
    await expect(page).toHaveURL(new RegExp(`conversationId=${conversation}`))
    expect(await (await page.request.get(`${base}/api/v1/conversations/${conversation}/runs`)).json()).toEqual([])
    const scope = page.getByRole("combobox", { name: "数据源范围" })
    await expect(scope).toHaveValue(String(sources[0].id))
    await expect(scope.getByRole("option", { name: sources[1].name })).toHaveCount(0)

    const prompts = [
      "列出你当前有权访问的数据源名称，只读取平台登记信息，不连接或查询数据库。用一句简短中文回答。",
      "只复述刚才那个数据源的名称，不调用工具。",
      "重新确认你现在能访问哪些数据源，只读取平台登记信息，用一句简短中文回答。",
    ]
    for (let index = 0; index < prompts.length; index++) {
      if (index === 2) {
        const revoked = await page.request.patch(`${base}/api/v1/agents/${savedAgent.id}`, { data: { datasource_ids: [] } })
        expect(revoked.status()).toBe(200)
        await page.reload()
        await expect(page.getByText("部分数据源已不在 Agent 授权范围，请重新选择范围后再发送。")).toBeVisible()
        await expect(page.getByRole("textbox", { name: "消息", exact: true })).toBeDisabled()
        await page.getByRole("combobox", { name: "数据源范围" }).selectOption("authorized")
      }
      const started = performance.now()
      await page.getByRole("textbox", { name: "消息", exact: true }).fill(prompts[index])
      const pending = page.waitForResponse(response => /\/runs$/.test(response.url()) && response.request().method() === "POST")
      await page.getByRole("button", { name: "发送", exact: true }).click()
      const accepted = await pending; expect(accepted.status()).toBe(202)
      const run = await accepted.json()
      const report: Record<string, unknown> = { prompt: prompts[index], runId: run.id }
      reports.push(report)
      let state
      await expect.poll(async () => {
        state = await (await page.request.get(`${base}/api/v1/runs/${run.id}`)).json()
        report.state = state
        return state.status
      }, { timeout: 120_000, intervals: [250, 500, 1000] }).toBe("finished")
      const result = report.state as { tool_calls: { name: string; result: { content: { id: number }[] } }[] }
      if (index === 1) expect(result.tool_calls).toEqual([])
      else {
        expect(result.tool_calls.some(call => call.name === "list_datasources")).toBe(true)
        for (const call of result.tool_calls) expect(call.result.content.map(item => item.id)).toEqual(index === 0 ? [sources[0].id] : [])
      }
      const article = page.locator(`[data-run-id="${run.id}"]`)
      await expect(article.getByRole("button", { name: "停止", exact: true })).toHaveCount(0)
      report.transcript = await article.innerText()
      report.seconds = (performance.now() - started) / 1000
      report.events = (await (await page.request.get(`${base}/api/v1/runs/${run.id}/events`)).text())
      await page.reload()
      await expect.poll(() => page.locator(`[data-run-id="${run.id}"]`).innerText()).toBe(report.transcript)
      expect((await (await page.request.get(`${base}/api/v1/conversations/${conversation}/runs`)).json()).length).toBe(index + 1)
    }
    await page.screenshot({ path: info.outputPath("custom-agent-desktop.png"), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(page.getByRole("textbox", { name: "消息", exact: true })).toBeVisible()
    await expect(page.locator("[data-message-id]").last()).toBeInViewport()
    await expect.poll(() => page.getByTestId("run-viewport").evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(2)
    expect(await page.evaluate("document.documentElement.scrollWidth <= innerWidth")).toBe(true)
    await page.screenshot({ path: info.outputPath("custom-agent-mobile.png"), fullPage: true })
    expect(errors).toEqual([]); expect(legacyRequests).toEqual([])
  } finally {
    const path = info.outputPath("real-custom-agent-transcripts.json")
    await writeFile(path, JSON.stringify({ agent, conversation, sources: sources.map(({ id, name }) => ({ id, name })), reports, errors, legacyRequests, acceptanceComplete: false }, null, 2))
    await info.attach("real-custom-agent-transcripts", { path, contentType: "application/json" })
  }
})
