// Trusted parser/browser harness. Generated modules are never imported by Node.
import { createRequire } from "node:module"
import path from "node:path"

const mode = process.argv[2]
const modules = process.argv[3]
const require = createRequire(path.join(modules, "package.json"))
const result = value => process.stdout.write(JSON.stringify(value) + "\n")
if (mode === "browser-path") {
  result({ path: require("@playwright/test").chromium.executablePath() })
} else {
  let input = ""
  for await (const chunk of process.stdin) input += chunk
  const data = JSON.parse(input)
  if (mode === "compile") {
    try {
      const esbuild = require("esbuild")
      result({ event: "ready" })
      const allowed = new Set(["react", "react/jsx-runtime", "react-dom/client", "recharts", "lucide-react"])
      const sdk = `export function invoke(name, payload = {}) {
        if (window.parent === window) return Promise.reject(new Error('Page host bridge is unavailable'));
        const id = crypto.randomUUID();
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => { window.removeEventListener('message', receive); reject(new Error('Page invocation was not confirmed')); }, 15000);
          function receive(event) {
            if (event.source !== window.parent || event.data?.type !== 'praxis.page.result' || event.data.id !== id) return;
            clearTimeout(timer); window.removeEventListener('message', receive);
            if (event.data.error) reject(new Error(event.data.error)); else resolve(event.data.value);
          }
          window.addEventListener('message', receive);
          window.parent.postMessage({ type: 'praxis.page.invoke', id, name, payload }, '*');
        });
      }`
      const entry = `import React from 'react'; import { createRoot } from 'react-dom/client'; import Page from './main.tsx'; createRoot(document.getElementById('root')).render(React.createElement(Page));`
      const files = data.files
      const built = await esbuild.build({
        entryPoints: ["__entry__"], outfile: "page.js", bundle: true, write: false, platform: "browser", format: "iife",
        jsx: "automatic", logLevel: "silent", minify: false, sourcemap: false,
        define: { "process.env.NODE_ENV": '"production"' },
        plugins: [{ name: "page-workspace", setup(build) {
          build.onResolve({ filter: /^__entry__$/ }, () => ({ path: "__entry__", namespace: "page" }))
          build.onResolve({ filter: /.*/, namespace: "page" }, async args => {
            if (args.path === "@praxis/page") return { path: "__sdk__", namespace: "page" }
            if (allowed.has(args.path)) return build.resolve(args.path, { kind: args.kind, resolveDir: modules })
            if (!args.path.startsWith("./") && !args.path.startsWith("../")) return { errors: [{ text: `Import is not allowed: ${args.path}` }] }
            const target = path.posix.normalize(path.posix.join(path.posix.dirname(args.importer), args.path))
            if (target.startsWith("../") || target.startsWith("/")) return { errors: [{ text: "Import escapes Page workspace" }] }
            const found = [target, ...[".tsx", ".ts", ".jsx", ".js", ".css", "/index.tsx", "/index.ts"].map(suffix => target + suffix)].find(name => Object.hasOwn(files, name))
            return found ? { path: found, namespace: "page" } : { errors: [{ text: `Workspace module not found: ${target}` }] }
          })
          build.onLoad({ filter: /.*/, namespace: "page" }, args => ({ contents: args.path === "__entry__" ? entry : args.path === "__sdk__" ? sdk : files[args.path], loader: args.path.startsWith("__") ? "js" : path.extname(args.path).slice(1) }))
        } }],
      })
      const js = built.outputFiles.find(file => file.path.endsWith(".js"))?.text
      if (!js) throw new Error("Compiler produced no JavaScript")
      const css = built.outputFiles.find(file => file.path.endsWith(".css"))?.text ?? ""
      const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><style>${css.replace(/<\/style/gi, "<\\/style")}</style></head><body><div id="root"></div><script>${js.replace(/<\/script/gi, "<\\/script")}</script></body></html>`
      result({ status: "passed", executed: true, html, warnings: built.warnings.map(item => item.text) })
    } catch (error) {
      result({ status: "failed", executed: true, diagnostic: error.errors?.map(item => item.text).join("; ") ?? "Page compiler could not compile the sources" })
    }
  } else if (mode === "browser") {
    let browser
    let started = false
    try {
      const { chromium } = require("@playwright/test")
      browser = await chromium.launch({ executablePath: "/browser/chrome", chromiumSandbox: true, args: ["--disable-dev-shm-usage"] })
      started = true; result({ event: "ready" })
      const context = await browser.newContext({ serviceWorkers: "block", acceptDownloads: false })
      await context.route("**/*", route => route.abort())
      const page = await context.newPage()
      const errors = []
      page.on("pageerror", error => errors.push(error.message.slice(0, 1000)))
      context.on("page", popup => { if (popup !== page) void popup.close() })
      await page.setContent(data.html, { waitUntil: "load", timeout: 5000 })
      await page.locator("#root > *").first().waitFor({ state: "visible", timeout: 5000 })
      result({ status: errors.length ? "failed" : "passed", executed: true, diagnostic: errors.join("; "), scope: "isolated initial DOM render; not a business interaction or live binding test" })
    } catch (error) {
      result({ status: started ? "failed" : "unavailable", executed: started, diagnostic: started ? "Isolated Page initial render failed" : "Isolated browser could not start" })
    } finally { await browser?.close() }
  } else {
    throw new Error("Unknown check mode")
  }
}
