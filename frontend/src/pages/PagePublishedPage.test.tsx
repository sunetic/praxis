import { screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"
import { renderWithShell as render } from "@/test/renderWithShell"
import { pageArtifactsApi } from "@/lib/pageArtifacts"
import { PagePublishedPage } from "./PagePublishedPage"

afterEach(() => vi.restoreAllMocks())
it("renders only the published immutable artifact without adding a theme or draft HTML", async () => {
  vi.spyOn(pageArtifactsApi, "published").mockResolvedValue({ page: { id: 1, name: "Published", status: "published" }, release: { id: 7, artifact_payload: { files: {}, bindings: {}, html: "<main>Exact release</main>", artifact_hash: "release", revision_id: "r1" } } })
  render(<MemoryRouter initialEntries={["/page/1"]}><Routes><Route path="/page/:pageId" element={<PagePublishedPage />} /></Routes></MemoryRouter>)
  const frame = await screen.findByTitle("查看发布版本")
  expect(frame).toHaveAttribute("srcdoc", "<main>Exact release</main>")
  expect(frame).toHaveAttribute("sandbox", "allow-scripts")
  expect(screen.getByRole("link", { name: "编辑页面" })).toHaveAttribute("href", "/page/workspace/1")
})

it("does not fall back to drafts when the release cannot be read", async () => {
  vi.spyOn(pageArtifactsApi, "published").mockRejectedValue(new Error("offline"))
  render(<MemoryRouter initialEntries={["/page/1"]}><Routes><Route path="/page/:pageId" element={<PagePublishedPage />} /></Routes></MemoryRouter>)
  expect(await screen.findByRole("alert")).toHaveTextContent("发布页面不存在")
  expect(document.querySelector("iframe")).not.toBeInTheDocument()
})
