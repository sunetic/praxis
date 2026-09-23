import { api } from "./api"
import type { ArtifactCheck } from "./functionArtifacts"

export type PageBinding = { function_id: number; release_id: number | null; revision_id: string | null }
export type PageSource = { files: Record<string, string>; bindings: Record<string, PageBinding> }
export type PageValidation = { id: string; revision_id: string; revision_hash: string; checks: (ArtifactCheck & { applicable?: boolean })[] }
export type PageDraft = PageSource & {
  page_id: number; name: string; revision_id: string | null; revision_hash: string
  current_release_id: number | null; released_revision_id: string | null
  changed_files: string[]; bindings_changed: boolean; artifact_hash: string | null; validation: PageValidation | null
  owned_functions: { id: number; name: string; current_release_id: number | null }[]
}
export type PagePreview = { page_id: number; revision_id: string; validation_id: string; artifact_hash: string; html: string }
export type PublishedPage = { page: { id: number; name: string; status: string }; release: { id: number; artifact_payload: PageSource & { revision_id: string; artifact_hash: string; html: string } } }
export const pageArtifactsApi = {
  read: async (id: number): Promise<PageDraft> => (await api.get(`/pages/${id}/draft`)).data,
  save: async (id: number, data: { expected_revision: string; source: PageSource }) => (await api.put(`/pages/${id}/draft`, data)).data,
  validate: async (id: number, expected_revision: string): Promise<PageValidation> => (await api.post(`/pages/${id}/validate`, { expected_revision })).data,
  preview: async (id: number, revision_id: string): Promise<PagePreview> => (await api.get(`/pages/${id}/preview`, { params: { revision_id } })).data,
  publish: async (id: number, data: { expected_revision: string; validation_id: string }) => (await api.post(`/pages/${id}/publish`, data)).data,
  published: async (id: number): Promise<PublishedPage> => (await api.get(`/pages/${id}/published`)).data,
}

export function canPublishPage(draft: PageDraft): boolean {
  const report = draft.validation
  const bound = Object.keys(draft.bindings).length > 0
  const required = ["source_compile", "function_bindings", "browser_runtime", ...(bound ? ["binding_runtime"] : [])]
  return Boolean(draft.revision_id && draft.artifact_hash && report && report.revision_id === draft.revision_id && report.revision_hash === draft.revision_hash
    && required.every(name => report.checks.some(check => check.name === name))
    && report.checks.filter(check => bound || check.name !== "binding_runtime").every(check => check.executed && check.status === "passed"))
}
