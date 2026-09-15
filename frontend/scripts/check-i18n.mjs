import { readFile, writeFile } from "node:fs/promises"
import path from "node:path"

import { ESLint } from "eslint"

const root = process.cwd()
const baselinePath = path.join(root, "eslint.i18n.baseline.json")
const eslint = new ESLint({
  cwd: root,
  overrideConfigFile: "eslint.i18n.config.js",
})

function nodeSource(result, message) {
  const lines = result.source.split(/\r?\n/)
  const startLine = message.line - 1
  const endLine = (message.endLine || message.line) - 1
  const endColumn = message.endColumn || message.column + 1
  if (startLine === endLine) {
    return lines[startLine].slice(message.column - 1, endColumn - 1).trim()
  }
  return lines
    .slice(startLine, endLine + 1)
    .map((line, index) => {
      if (index === 0) return line.slice(message.column - 1)
      if (index === endLine - startLine) return line.slice(0, endColumn - 1)
      return line
    })
    .join("\n")
    .trim()
}

function collect(results) {
  const entries = new Map()
  for (const result of results) {
    if (!result.source) continue
    const file = path.relative(root, result.filePath).split(path.sep).join("/")
    for (const message of result.messages) {
      if (message.ruleId !== "no-restricted-syntax" || message.severity !== 2) continue
      const source = nodeSource(result, message)
      const key = JSON.stringify([file, message.message, source])
      const entry = entries.get(key) || {
        file,
        message: message.message,
        source,
        count: 0,
        locations: [],
      }
      entry.count += 1
      entry.locations.push(`${file}:${message.line}:${message.column}`)
      entries.set(key, entry)
    }
  }
  return entries
}

function serialize(entries) {
  return [...entries.values()]
    .map(({ locations: _locations, ...entry }) => entry)
    .sort((left, right) =>
      `${left.file}\0${left.message}\0${left.source}`.localeCompare(
        `${right.file}\0${right.message}\0${right.source}`,
      ),
    )
}

const current = collect(await eslint.lintFiles(["src"]))
let baselineEntries = []
try {
  baselineEntries = JSON.parse(await readFile(baselinePath, "utf8"))
} catch (error) {
  if (error.code !== "ENOENT") throw error
}
const baseline = new Map(
  baselineEntries.map((entry) => [JSON.stringify([entry.file, entry.message, entry.source]), entry]),
)

if (process.argv.includes("--update")) {
  const additions = []
  for (const [key, entry] of current) {
    if (entry.count > (baseline.get(key)?.count || 0)) {
      additions.push(`${entry.locations.join(", ")}: ${JSON.stringify(entry.source)}`)
    }
  }
  if (baseline.size && additions.length) {
    console.error(
      `Refusing to expand the i18n debt baseline:\n${additions.join("\n")}\n` +
        "Translate the new copy instead.",
    )
    process.exit(1)
  }
  await writeFile(baselinePath, `${JSON.stringify(serialize(current), null, 2)}\n`)
  console.log(`Updated ${path.relative(root, baselinePath)} with ${current.size} known fingerprints.`)
  process.exit(0)
}

if (!baseline.size) {
  console.error("Missing i18n baseline. Restore eslint.i18n.baseline.json.")
  process.exit(1)
}
const failures = []

for (const [key, entry] of current) {
  const allowed = baseline.get(key)?.count || 0
  if (entry.count > allowed) {
    failures.push(
      `New untranslated UI copy at ${entry.locations.join(", ")}: ${JSON.stringify(entry.source)}`,
    )
  }
}
for (const [key, entry] of baseline) {
  const remaining = current.get(key)?.count || 0
  if (remaining < entry.count) {
    failures.push(
      `i18n debt was reduced in ${entry.file}; run npm run lint:i18n:update to shrink the baseline.`,
    )
  }
}

if (failures.length) {
  console.error(failures.join("\n"))
  process.exit(1)
}

const knownCount = [...current.values()].reduce((total, entry) => total + entry.count, 0)
console.log(`i18n guard passed (${knownCount} explicitly baselined legacy violations, no regression).`)
