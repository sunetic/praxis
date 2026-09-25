import { readFile, stat } from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"

const ENTRY_BUDGET_BYTES = 600_000
const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const distDir = path.resolve(scriptDir, "../dist")
const html = await readFile(path.join(distDir, "index.html"), "utf8")
const match = html.match(/<script[^>]+src="([^"]+\.js)"/)

if (!match) {
  throw new Error("Unable to find the production entry script in dist/index.html")
}

const entryPath = path.join(distDir, match[1].replace(/^\//, ""))
const { size } = await stat(entryPath)
const format = (bytes) => `${(bytes / 1000).toFixed(1)} kB`

if (size > ENTRY_BUDGET_BYTES) {
  throw new Error(
    `Entry bundle ${format(size)} exceeds the ${format(ENTRY_BUDGET_BYTES)} budget: ${path.basename(entryPath)}`,
  )
}

console.log(
  `Entry bundle ${format(size)} is within the ${format(ENTRY_BUDGET_BYTES)} budget: ${path.basename(entryPath)}`,
)
