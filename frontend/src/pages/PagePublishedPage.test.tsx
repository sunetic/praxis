import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"

import { PagePublishedPage } from "./PagePublishedPage"

const { pagesApi } = vi.hoisted(() => ({
  pagesApi: {
    getPublished: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  pagesApi,
}))

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>
}

describe("PagePublishedPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pagesApi.getPublished.mockResolvedValue({
      page: { id: 1, name: "Page-1", status: "published" },
      release: {
        artifact_payload: {
          kind: "runtime_page",
          runtime: {
            framework: "html",
            preview_html: "<!doctype html><html><body><main><p>发布内容</p></main></body></html>",
          },
          source: { language: "tsx", code: "export default function Page(){return <main/>}" },
          config: { title: "Page-1", description: "" },
        },
      },
    })
  })

  it("navigates to build workspace when clicking edit", async () => {
    render(
      <MemoryRouter initialEntries={["/page/1"]}>
        <Routes>
          <Route
            path="/page/:pageId"
            element={
              <>
                <PagePublishedPage />
                <LocationProbe />
              </>
            }
          />
          <Route path="/page/workspace/:pageId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    )

    await screen.findByTitle("Published Runtime Page")
    await userEvent.click(screen.getByRole("button", { name: "编辑" }))
    await waitFor(() =>
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/page/workspace/1?from=published")
    )
  })
})
