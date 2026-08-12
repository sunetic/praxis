import { screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"

import { renderWithShell as render } from "@/test/renderWithShell"
import { KnowledgeListPage } from "./KnowledgeListPage"

const { knowledgeApi, knowledgePackApi } = vi.hoisted(() => ({
  knowledgeApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  knowledgePackApi: {
    list: vi.fn(),
    install: vi.fn(),
    uninstall: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({ knowledgeApi, knowledgePackApi }))

describe("KnowledgeListPage versioned packs", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    knowledgeApi.list.mockResolvedValue([
      {
        id: 1,
        name: "MySQL Documentation",
        description: "Versioned MySQL documentation",
        tags: ["mysql"],
        source: "pack",
        pack_id: "mysql-docs",
        document_count: 240,
        created_at: "2026-08-12T00:00:00Z",
        updated_at: "2026-08-12T00:00:00Z",
      },
    ])
    knowledgePackApi.list.mockResolvedValue([
      {
        id: "mysql-docs",
        name: "MySQL Documentation",
        description: "Versioned MySQL documentation",
        tags: ["mysql"],
        db_type: "mysql",
        repo_url: "https://github.com/example/mysql-docs",
        branch: "8.4",
        subdirectory: "docs",
        license: "CC BY 4.0",
        estimated_doc_count: 240,
        estimated_size_mb: 2.5,
        versions: [
          { branch: "8.4", label: "8.4" },
          { branch: "8.0", label: "8.0" },
        ],
        default_version: "8.4",
        status: "installed",
        kb_id: 1,
      },
    ])
  })

  it("shows supported versions as read-only badges", async () => {
    render(
      <MemoryRouter>
        <KnowledgeListPage />
      </MemoryRouter>
    )

    expect(await screen.findByText("v8.4")).toBeInTheDocument()
    expect(screen.getByText("v8.0")).toBeInTheDocument()
    expect(screen.getByText("可用版本")).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument())
  })
})
