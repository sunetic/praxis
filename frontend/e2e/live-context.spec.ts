import { test, expect } from "@playwright/test"
import { writeFile } from "node:fs/promises"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

test("a real compressed conversation restores original replies without exposing summary text", async ({ page }, info) => {
  const base = process.env.PRAXIS_BROWSER_LIVE_URL
  const conversationId = process.env.PRAXIS_BROWSER_CONTEXT_ID
  test.skip(!base || !conversationId, "Provide an isolated server and a completed real context-smoke conversation")
  await page.addInitScript(() => localStorage.setItem("praxis.locale", "zh-CN"))
  const response = await page.request.get(`${base}/api/v1/conversations/${conversationId}/runs`)
  expect(response.ok()).toBe(true)
  const runs = await response.json() as { id: string; status: string; output: string }[]
  expect(runs.length).toBeGreaterThan(1)
  expect(runs.every(run => run.status === "finished")).toBe(true)
  const last = runs.at(-1)!
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  await page.goto(`${base}/chat?conversationId=${conversationId}`)
  await expect(page.locator("[data-run-id]")).toHaveCount(runs.length)
  const article = page.locator(`[data-run-id="${last.id}"]`)
  await expect(article.locator("[data-message-id]")).toHaveCount(1)
  // Compare rendered text, not literal Markdown delimiters. Build the expected
  // result independently of the product's event/history projection.
  const expectedHtml = renderToStaticMarkup(createElement(ReactMarkdown, {
    remarkPlugins: [remarkGfm], children: last.output,
  }))
  const expectedText = await page.evaluate(html => new DOMParser().parseFromString(html, "text/html").body.textContent ?? "", expectedHtml)
  await expect(article.locator("[data-message-id]")).toHaveText(expectedText)
  const eventResponse = await page.request.get(`${base}/api/v1/runs/${last.id}/events`)
  expect(eventResponse.ok()).toBe(true)
  const events = (await eventResponse.text()).split("\n")
    .filter(line => line.startsWith("data:"))
    .map(line => JSON.parse(line.slice(5)) as { type: string; text?: string })
  expect(events.filter(event => event.type === "assistant_delta").map(event => event.text ?? "").join("")).toBe(last.output)
  await expect(page.getByText("Historical conversation summary", { exact: false })).toHaveCount(0)
  const text = await article.innerText()
  await page.reload()
  await expect.poll(() => article.innerText()).toBe(text)
  await expect(page.getByRole("button", { name: "停止", exact: true })).toHaveCount(0)
  expect(errors).toEqual([])
  const evidence = info.outputPath("restored-context.json")
  await writeFile(evidence, JSON.stringify({ conversationId, runs, browserErrors: errors, finalVisibleText: text, acceptanceComplete: false }, null, 2))
  await info.attach("restored-context", { path: evidence, contentType: "application/json" })
  await page.screenshot({ path: info.outputPath("restored-context.png"), fullPage: true })
})
