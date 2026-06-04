import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { ShellI18nProvider } from "@/i18n/shellI18n"

import { SkillsPage } from "./SkillsPage"

const { skillsApi, toast } = vi.hoisted(() => ({
  skillsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}))

vi.mock("@/lib/api", () => ({
  skillsApi,
}))

vi.mock("sonner", () => ({
  toast,
}))

describe("SkillsPage", () => {
  it("keeps table header visible and shows skeleton rows while loading", async () => {
    let resolveList: (value: Array<Record<string, unknown>>) => void = () => {}
    skillsApi.list.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveList = resolve
        })
    )

    render(
      <ShellI18nProvider>
        <SkillsPage />
      </ShellI18nProvider>
    )

    expect(screen.getByRole("columnheader", { name: "名称" })).toBeInTheDocument()
    expect(screen.queryByText("加载中...")).not.toBeInTheDocument()
    expect(document.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)

    resolveList([
      {
        name: "ob-slow-query",
        version: "1.0.0",
        description: "用于慢 SQL 诊断的通用能力",
        database: "oceanbase",
        always_apply: false,
        prompt: "diagnose",
        source: "custom",
      },
    ])

    await waitFor(() => {
      expect(screen.getByText("ob-slow-query")).toBeInTheDocument()
    })
  })

  it("shows table skeleton immediately when source filter changes", async () => {
    const initialSkills = [
      {
        name: "ob-slow-query",
        version: "1.0.0",
        description: "用于慢 SQL 诊断的通用能力",
        database: "oceanbase",
        always_apply: false,
        prompt: "diagnose",
        source: "custom",
      },
      {
        name: "builtin-review",
        version: "1.0.0",
        description: "内建能力",
        database: "general",
        always_apply: true,
        prompt: "builtin",
        source: "built_in",
      },
    ]
    let resolveReload: (value: Array<Record<string, unknown>>) => void = () => {}
    skillsApi.list.mockResolvedValueOnce(initialSkills).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveReload = resolve
        })
    )

    render(
      <ShellI18nProvider>
        <SkillsPage />
      </ShellI18nProvider>
    )

    await waitFor(() => {
      expect(screen.getByText("ob-slow-query")).toBeInTheDocument()
      expect(screen.getByText("builtin-review")).toBeInTheDocument()
    })

    act(() => {
      const selects = screen.getAllByRole("combobox")
      fireEvent.change(selects[0], { target: { value: "built_in" } })
    })

    expect(document.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
    expect(screen.queryByText("ob-slow-query")).not.toBeInTheDocument()

    resolveReload(initialSkills)

    await waitFor(() => {
      expect(screen.getByText("builtin-review")).toBeInTheDocument()
    })
  })
})
