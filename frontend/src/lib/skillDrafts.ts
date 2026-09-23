import { api } from "./api"

export type SkillDraftContent = {
  name: string; version: string; description: string; database: string
  always_apply: boolean; prompt: string
}
export type SkillDraft = { id: string; revision: string; content: SkillDraftContent; updated_at: number }
export const skillDraftsApi = {
  create: async (): Promise<SkillDraft> => (await api.post("/skill-drafts")).data,
  read: async (id: string): Promise<SkillDraft> => (await api.get(`/skill-drafts/${encodeURIComponent(id)}`)).data,
  write: async (id: string, expected_revision: string, content: SkillDraftContent): Promise<SkillDraft> =>
    (await api.patch(`/skill-drafts/${encodeURIComponent(id)}`, { expected_revision, content })).data,
}
