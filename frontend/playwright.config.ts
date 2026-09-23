import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: "test-results",
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    ...devices["Desktop Chrome"],
    locale: "zh-CN",
    trace: "on",
    screenshot: "only-on-failure",
    // An installed full Chromium is sufficient; do not require headless-shell too.
    channel: "chromium",
  },
})
