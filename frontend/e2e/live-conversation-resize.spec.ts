import { test, expect } from "@playwright/test"

test("existing real conversation follows viewport resize without pulling a reader down", async ({ page }, info) => {
  const base = process.env.PRAXIS_LIVE_URL
  const id = process.env.PRAXIS_LIVE_CONVERSATION
  test.skip(!base || !id, "Requires an existing real conversation; creates no runs")
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  await page.goto(`${base}/chat?conversationId=${id}`)
  const viewport = page.getByTestId("run-viewport")
  const last = page.locator("[data-message-id]").last()
  await expect(last).toBeInViewport()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => viewport.evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(2)
  await expect(last).toBeInViewport()
  await page.screenshot({ path: info.outputPath("mobile-latest-answer.png"), fullPage: true })
  await viewport.evaluate(node => { node.scrollTop = 0 })
  await expect(page.getByRole("button", { name: "回到底部", exact: true })).toBeVisible()
  await page.setViewportSize({ width: 420, height: 740 })
  expect(await viewport.evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeGreaterThan(64)
  await expect(page.getByRole("button", { name: "回到底部", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "回到底部", exact: true }).click()
  await expect(last).toBeInViewport()
})
