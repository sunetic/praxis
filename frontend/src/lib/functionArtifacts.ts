import { api } from "./api"

export type ArtifactCheck = { name: string; status: string; executed: boolean; diagnostic?: string }
export type FunctionValidation = { id: string; revision_id: string; revision_hash: string; checks: ArtifactCheck[]; created_at: number }
export type FunctionDraft = {
  function_id: number; name: string; slug: string; code: string; dependencies: Record<string, unknown>
  revision_id: string | null; revision_hash: string; current_release_id: number | null
  released_revision_id: string | null; changed_files: string[]; validation: FunctionValidation | null
}

export const functionArtifactsApi = {
  read: async (id: number): Promise<FunctionDraft> => (await api.get(`/functions/${id}/draft`)).data,
  save: async (id: number, data: { expected_revision: string; code: string; dependencies: Record<string, unknown> }) => (await api.put(`/functions/${id}/draft`, data)).data,
  validate: async (id: number, data: { expected_revision: string; payload: Record<string, unknown> }): Promise<FunctionValidation> => (await api.post(`/functions/${id}/verify`, data)).data,
  publish: async (id: number, data: { expected_revision: string; validation_id: string }) => (await api.post(`/functions/${id}/release`, data)).data,
}

/** Display eligibility only. The server repeats this check in the publication transaction. */
export function canPublishDraft(draft: FunctionDraft): boolean {
  const report = draft.validation
  return Boolean(draft.revision_id && report && report.revision_id === draft.revision_id && report.revision_hash === draft.revision_hash
    && ["python_syntax", "entrypoint", "controlled_runtime"].every(name => report.checks.some(check => check.name === name))
    && report.checks.every(check => check.executed && check.status === "passed"))
}
