import { describe, expect, it } from "vitest"

import { ensurePagePreviewTheme } from "./pageTheme"

describe("ensurePagePreviewTheme", () => {
  it("injects platform style scaffold for html without head style", () => {
    const html = "<!doctype html><html><body><main><p>hello</p></main></body></html>"
    const themed = ensurePagePreviewTheme(html)
    expect(themed).toContain("praxis-preview-theme")
    expect(themed).toContain("<meta charset='utf-8' />")
  })

  it("builds default preview when html is empty", () => {
    const themed = ensurePagePreviewTheme("")
    expect(themed).toContain("praxis-preview-theme")
    expect(themed).toContain("<main>")
    expect(themed).not.toContain("描述页面需求后，这里会显示实时结果。")
  })
})
