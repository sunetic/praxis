import { test, expect } from "@playwright/test"

test("real Skill workspace restores native runs and preserves local edits on a revision conflict", async ({ page }, info) => {
  const base = process.env.PRAXIS_LIVE_URL
  const draftId = process.env.PRAXIS_LIVE_SKILL_DRAFT_ID
  const conversationId = process.env.PRAXIS_LIVE_CONVERSATION
  test.skip(!base || !draftId || !conversationId, "Requires an isolated completed Skill smoke workspace")
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  const response = await page.request.get(base + "/api/v1/skill-drafts/" + draftId)
  expect(response.ok()).toBe(true)
  const draft = await response.json() as { revision: string; content: { name: string; prompt: string; description: string; database: string } }
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  await page.goto(base + "/skills/builder?draftId=" + draftId + "&conversationId=" + conversationId)
  await expect(page.getByLabel("名称", { exact: true })).toHaveValue(draft.content.name)
  await expect(page.locator("#builder-prompt")).toHaveValue(draft.content.prompt)
  await expect(page.locator("[data-run-id]")).toHaveCount(3)
  await page.reload()
  await expect(page.locator("#builder-prompt")).toHaveValue(draft.content.prompt)
  await expect(page.locator("[data-run-id]")).toHaveCount(3)
  await page.screenshot({ path: info.outputPath("skill-desktop.png"), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
  await page.locator("#builder-description").fill("Browser local edit kept during conflict")
  const changed = await page.request.patch(base + "/api/v1/skill-drafts/" + draftId, { data: {
    expected_revision: draft.revision, content: { ...draft.content, description: "Browser concurrent edit stored remotely" },
  } })
  expect(changed.ok()).toBe(true)
  await page.getByRole("button", { name: "保存草稿", exact: true }).click()
  await expect(page.getByRole("button", { name: "安装 Skill", exact: true })).toBeDisabled()
  await expect(page.locator("#builder-description")).toHaveValue("Browser local edit kept during conflict")
  await page.getByRole("button", { name: "放弃本地修改，载入最新草稿", exact: true }).click()
  await expect(page.locator("#builder-description")).toHaveValue("Browser concurrent edit stored remotely")
  await page.locator("#builder-prompt").scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath("skill-mobile.png"), fullPage: true })
  expect(errors).toEqual([])
})
