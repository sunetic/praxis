import { expect, test } from "@playwright/test"
import { writeFile } from "node:fs/promises"


test("MCP lifecycle: connect, edit, invoke from Chat, and delete", async ({ page }, info) => {
  const base = process.env.PRAXIS_BROWSER_LIVE_URL
  const mcpUrl = process.env.PRAXIS_MCP_LIVE_URL
  test.skip(!base || !mcpUrl, "Requires an isolated Praxis service and MCP fixture")
  if (!base || !mcpUrl) return
  test.setTimeout(300_000)

  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  const report: Record<string, unknown> = { base, mcpUrl, browserErrors: errors }
  const name = `Browser MCP ${Date.now()}`
  const editedName = `${name} updated`
  let serviceId: number | undefined

  try {
    const onboarding = await (await page.request.get(`${base}/api/v1/onboarding/status`)).json()
    expect(onboarding.completed).toBe(true)
    const datasources = await (await page.request.get(`${base}/api/v1/datasources`)).json()
    expect(datasources.length).toBeGreaterThan(0)
    const datasource = datasources[0]
    expect(datasource.cluster_key).toBeTruthy()

    await page.goto(`${base}/mcp`)
    await expect(page.getByRole("link", { name: "MCP", exact: true })).toBeVisible()
    await page.getByRole("button", { name: "新增 MCP Server" }).click()
    const createDialog = page.getByRole("dialog", { name: "连接 MCP Server" })
    await createDialog.getByRole("textbox", { name: "名称" }).fill(name)
    await createDialog.getByRole("textbox", { name: "基础 URL" }).fill(mcpUrl)
    await createDialog.getByRole("combobox", { name: "关联资源", exact: true }).click()
    await page.getByRole("option", { name: datasource.cluster_key, exact: true }).click()

    await createDialog.getByRole("button", { name: "测试 MCP 连接" }).click()
    await expect(page.getByText("MCP Server 连接成功，发现 1 个工具", { exact: true })).toBeVisible()
    const createdResponse = page.waitForResponse(response => response.url().endsWith("/api/v1/services") && response.request().method() === "POST")
    await createDialog.getByRole("button", { name: "创建", exact: true }).click()
    expect((await createdResponse).status()).toBe(201)
    await expect(page.getByText(name, { exact: true })).toBeVisible()

    const servicesAfterCreate = await (await page.request.get(`${base}/api/v1/services`)).json()
    const created = servicesAfterCreate.find((service: { name: string }) => service.name === name)
    expect(created).toMatchObject({
      service_type: "mcp",
      resource_ref: `cluster:${datasource.cluster_key}`,
      status: "active",
    })
    serviceId = created.id

    await page.getByText(name, { exact: true }).click()
    const editDialog = page.getByRole("dialog", { name: "编辑 MCP Server" })
    await editDialog.getByRole("textbox", { name: "名称" }).fill(editedName)
    const updatedResponse = page.waitForResponse(response => response.url().endsWith(`/api/v1/services/${serviceId}`) && response.request().method() === "PATCH")
    await editDialog.getByRole("button", { name: "保存", exact: true }).click()
    expect((await updatedResponse).ok()).toBe(true)
    await expect(page.getByText(editedName, { exact: true })).toBeVisible()

    const conversationResponse = await page.request.post(`${base}/api/v1/conversations`, {
      data: { title: `Browser MCP lifecycle ${Date.now()}`, scene: { datasource_ids: [], mcp_service_ids: [] } },
    })
    expect(conversationResponse.status()).toBe(201)
    const conversation = await conversationResponse.json()
    await page.goto(`${base}/chat?conversationId=${conversation.id}`)
    await page.getByRole("combobox", { name: "数据源范围" }).selectOption(String(datasource.id))
    const mcpSelect = page.getByRole("combobox", { name: "MCP 服务" })
    await expect(mcpSelect).toBeEnabled()
    await mcpSelect.selectOption(String(serviceId))

    const prompt = "必须调用当前已授权的 MCP 工具 resolve_marker，参数 seed 设为 browser_e2e。禁止猜测；完成后只回复工具返回的 marker 原文。"
    await page.getByRole("textbox", { name: "消息", exact: true }).fill(prompt)
    const submitted = page.waitForResponse(response => /\/conversations\/[^/]+\/runs$/.test(response.url()) && response.request().method() === "POST")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const runResponse = await submitted
    expect(runResponse.status()).toBe(202)
    const run = await runResponse.json()

    let state: Record<string, unknown> = {}
    let approvalSubmitted = false
    await expect.poll(async () => {
      state = await (await page.request.get(`${base}/api/v1/runs/${run.id}`)).json()
      if (state.status === "waiting_approval" && !approvalSubmitted) {
        const approval = page.getByRole("button", { name: "批准此操作" })
        await expect(approval).toBeVisible({ timeout: 10_000 })
        await approval.click()
        approvalSubmitted = true
      }
      return state.status
    }, { timeout: 180_000, intervals: [300, 500, 1000] }).toBe("finished")

    const expectedMarker = "praxis-browser-mcp-browser_e2e"
    const toolCalls = state.tool_calls as Array<{ name: string; status: string; result: unknown }>
    const mcpCall = toolCalls.find(call => call.name.endsWith("resolve_marker"))
    expect(mcpCall).toBeTruthy()
    if (!mcpCall) throw new Error("MCP tool call was not recorded")
    expect(mcpCall.status).toBe("succeeded")
    expect(JSON.stringify(mcpCall.result)).toContain(expectedMarker)
    await expect(page.getByText(expectedMarker, { exact: true })).toBeVisible()
    report.run = state

    const observed = await (await page.request.get(new URL("/calls", mcpUrl).toString())).json()
    expect(observed).toContainEqual({ name: "resolve_marker", arguments: { seed: "browser_e2e" } })
    report.observedCalls = observed

    await page.goto(`${base}/mcp`)
    const row = page.getByRole("row").filter({ hasText: editedName })
    await row.getByRole("button", { name: "删除 MCP Server" }).click()
    const deleteDialog = page.getByRole("dialog", { name: "删除 MCP Server" })
    const deletedResponse = page.waitForResponse(response => response.url().endsWith(`/api/v1/services/${serviceId}`) && response.request().method() === "DELETE")
    await deleteDialog.getByRole("button", { name: "确认", exact: true }).click()
    expect((await deletedResponse).status()).toBe(204)
    await expect(page.getByText(editedName, { exact: true })).toHaveCount(0)
    serviceId = undefined
    expect(errors).toEqual([])
  } finally {
    if (serviceId) await page.request.delete(`${base}/api/v1/services/${serviceId}`).catch(() => undefined)
    const reportPath = info.outputPath("live-mcp-lifecycle.json")
    await writeFile(reportPath, JSON.stringify(report, null, 2))
    await info.attach("live-mcp-lifecycle", { path: reportPath, contentType: "application/json" })
    await page.screenshot({ path: info.outputPath("live-mcp-final.png"), fullPage: true }).catch(() => undefined)
  }
})
