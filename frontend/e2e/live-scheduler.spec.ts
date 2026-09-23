import { test, expect } from "@playwright/test"
import { writeFile } from "node:fs/promises"

test("scheduled Agent uses a native run and opens the same conversation for follow-up", async ({ page }, info) => {
  const base = process.env.PRAXIS_BROWSER_LIVE_URL
  test.skip(!base, "Set PRAXIS_BROWSER_LIVE_URL to an isolated configured product server")
  test.setTimeout(180_000)
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  const report: Record<string, unknown> = { kind: "live-scheduled-agent", acceptanceComplete: false }
  try {
    const agentResponse = await page.request.post(`${base}/api/v1/agents`, { data: {
      name: "Scheduled native smoke", prompt: "根据用户要求回答，诚实说明证据和限制。", tools: [], skills: [],
    } })
    expect(agentResponse.status()).toBe(201)
    const agent = await agentResponse.json()
    const created = await page.request.post(`${base}/api/v1/schedules`, { data: {
      name: "Native scheduler smoke", target_type: "agent", target_id: agent.id,
      schedule_type: "interval", interval_seconds: 3600, status: "paused", max_retries: 0,
      input_prompt: "用中文两句话解释数据库慢查询和死锁的区别。不要查询或修改任何数据库。",
    } })
    expect(created.status()).toBe(201)
    const schedule = await created.json()
    report.schedule = schedule
    const accepted = await page.request.post(`${base}/api/v1/schedules/${schedule.id}/run-now`)
    expect(accepted.ok()).toBe(true)
    const occurrence = await accepted.json()
    report.accepted = occurrence
    const paths = ["schedules", "agents", "functions", "datasources", "schedules/runs", "conversations"]
    const reads = await Promise.all(Array.from({ length: 3 }, () => paths).flat().map(async path => {
      const started = performance.now()
      const response = await page.request.get(`${base}/api/v1/${path}`, { timeout: 10_000 })
      return { path, status: response.status(), elapsedMs: performance.now() - started }
    }))
    report.concurrentReads = reads
    expect(reads.every(read => read.status === 200)).toBe(true)
    let record: Record<string, unknown> = {}
    await expect.poll(async () => {
      const records = await page.request.get(`${base}/api/v1/schedules/${schedule.id}/runs`)
      record = (await records.json()).find((item: { run_id: string }) => item.run_id === occurrence.run_id) ?? {}
      return record.status
    }, { timeout: 90_000, intervals: [200, 500, 1000] }).toBe("finished")
    report.record = record
    expect(record.runtime_status).toBe("finished")
    expect(record.retry_count).toBe(0)
    expect(typeof record.conversation_id).toBe("string")
    const nativeResponse = await page.request.get(`${base}/api/v1/runs/${record.runtime_run_id}`)
    expect(nativeResponse.ok()).toBe(true)
    const native = await nativeResponse.json()
    report.native = native
    expect(native.output).toBe(record.output_summary)
    expect(native.tool_calls).toEqual([])
    expect(native.output.length).toBeGreaterThan(0)
    const events = await page.request.get(`${base}/api/v1/runs/${record.runtime_run_id}/events`)
    report.events = (await events.text()).split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)))
    await page.goto(`${base}/scheduler/${schedule.id}`)
    await page.getByRole("tab", { name: "执行记录", exact: true }).click()
    await page.getByText(occurrence.run_id, { exact: true }).click()
    await expect(page.getByText("状态: 运行已结束", { exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "修复假 running" })).toHaveCount(0)
    await page.screenshot({ path: info.outputPath("scheduled-run.png"), fullPage: true })
    await page.getByRole("button", { name: "查看对话与处理审批", exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`conversationId=${record.conversation_id}`))
    await expect(page.locator(`[data-run-id="${native.id}"] [data-message-id]`).first()).toBeVisible()
    report.transcript = await page.locator(`[data-run-id="${native.id}"]`).innerText()
    await page.getByRole("textbox", { name: "消息", exact: true }).fill("只解释刚才提到的死锁，补一个简短例子，不要实际执行。")
    const submitted = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/conversations/${record.conversation_id}/runs`))
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const followup = await (await submitted).json()
    await expect.poll(async () => {
      const state = await page.request.get(`${base}/api/v1/runs/${followup.id}`)
      report.followup = await state.json()
      return (report.followup as { status: string }).status
    }, { timeout: 90_000 }).toBe("finished")
    await expect(page.locator(`[data-run-id="${followup.id}"] [data-message-id]`).first()).toBeVisible()
    const history = await page.request.get(`${base}/api/v1/conversations/${record.conversation_id}/runs`)
    expect((await history.json()).map((run: { id: string }) => run.id)).toEqual([native.id, followup.id])
    // A follow-up does not change which native occurrence the schedule reports.
    const refreshed = await page.request.get(`${base}/api/v1/schedules/${schedule.id}/runs`)
    expect((await refreshed.json())[0].runtime_run_id).toBe(native.id)
  } finally {
    const path = info.outputPath("scheduled-agent.json")
    await writeFile(path, JSON.stringify(report, null, 2))
    await info.attach("scheduled-agent", { path, contentType: "application/json" })
    await page.screenshot({ path: info.outputPath("scheduled-chat.png"), fullPage: true }).catch(() => undefined)
  }
})
