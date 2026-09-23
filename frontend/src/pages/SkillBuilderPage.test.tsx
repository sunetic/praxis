import { StrictMode } from "react"
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { renderWithShell } from "@/test/renderWithShell"
import { agentRunsApi, type RunEvent } from "@/lib/agentRuns"
import { skillsApi } from "@/lib/api"
import { skillDraftsApi, type SkillDraft } from "@/lib/skillDrafts"
import { SkillBuilderPage } from "./SkillBuilderPage"

const bridge = vi.hoisted(() => ({ onEvent: undefined as ((event: RunEvent) => void) | undefined }))
vi.mock("@/components/chat/RunConversationView", () => ({ RunConversationView: (props: { conversationId: string; onEvent: (event: RunEvent) => void }) => {
  bridge.onEvent = props.onEvent
  return <div data-testid="native-conversation">{props.conversationId}</div>
} }))
const original: SkillDraft = { id: "draft-1", revision: "revision-1", updated_at: 1, content: {
  name: "query-review", version: "1.0.0", description: "Review queries using evidence", database: "general", always_apply: false, prompt: "Use actual query results.",
} }
const conversation = { id: "native-1", title: "draft", scene: { skill_draft_ids: [original.id] }, created_at: 1, active_run_id: null }
const mount = (url = "/skills/builder") => renderWithShell(<StrictMode><MemoryRouter initialEntries={[url]}><SkillBuilderPage /></MemoryRouter></StrictMode>)

beforeEach(() => {
  vi.spyOn(skillDraftsApi, "create").mockResolvedValue(structuredClone(original))
  vi.spyOn(skillDraftsApi, "read").mockResolvedValue(structuredClone(original))
  vi.spyOn(skillDraftsApi, "write").mockImplementation(async (_id, _revision, content) => ({ ...original, revision: "revision-2", content }))
  vi.spyOn(agentRunsApi, "createConversation").mockResolvedValue(conversation)
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([conversation])
  vi.spyOn(skillsApi, "create").mockResolvedValue({} as never)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); bridge.onEvent = undefined })

it("initializes one native workspace without installing or parsing assistant text", async () => {
  mount()
  expect(await screen.findByTestId("native-conversation")).toHaveTextContent("native-1")
  expect(skillDraftsApi.create).toHaveBeenCalledOnce()
  expect(agentRunsApi.createConversation).toHaveBeenCalledWith(expect.any(String), { skill_draft_ids: [original.id], datasource_ids: [], knowledge_base_ids: [], service_ids: [] })
  await act(async () => bridge.onEvent?.({ run_id: "r", seq: 1, type: "assistant_delta", text: '{"skill_result":{"name":"do-not-parse"}}' }))
  expect(screen.getByLabelText("名称")).toHaveValue("query-review")
  expect(skillsApi.create).not.toHaveBeenCalled()
})

it("restores the exact draft/conversation without creating another session", async () => {
  mount("/skills/builder?draftId=draft-1&conversationId=native-1")
  await screen.findByTestId("native-conversation")
  expect(skillDraftsApi.create).not.toHaveBeenCalled()
  expect(agentRunsApi.createConversation).not.toHaveBeenCalled()
})

it("rejects a conversation bound to a different draft", async () => {
  vi.mocked(agentRunsApi.conversations).mockResolvedValue([{ ...conversation, scene: { skill_draft_ids: ["other"] } }])
  mount("/skills/builder?draftId=draft-1&conversationId=native-1")
  expect(await screen.findByRole("alert")).toHaveTextContent("无法加载")
  expect(screen.queryByTestId("native-conversation")).toBeNull()
})

it("preserves unsaved edits when a model tool saves a newer draft", async () => {
  mount()
  await screen.findByTestId("native-conversation")
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "local-edit" } })
  vi.mocked(skillDraftsApi.read).mockResolvedValue({ ...original, revision: "remote-new", content: { ...original.content, name: "remote-edit" } })
  await act(async () => bridge.onEvent?.({ run_id: "r", seq: 1, type: "tool_result" }))
  expect(screen.getByLabelText("名称")).toHaveValue("local-edit")
  expect(await screen.findByRole("button", { name: "安装 Skill" })).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: "放弃本地修改，载入最新草稿" }))
  await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("remote-edit"))
})

it("saves the exact manual revision without installing until explicitly requested", async () => {
  mount()
  await screen.findByTestId("native-conversation")
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "manual-review" } })
  await userEvent.click(screen.getByRole("button", { name: "保存草稿" }))
  await waitFor(() => expect(skillDraftsApi.write).toHaveBeenCalledWith(original.id, original.revision, { ...original.content, name: "manual-review" }))
  expect(skillsApi.create).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole("button", { name: "安装 Skill" }))
  await waitFor(() => expect(skillsApi.create).toHaveBeenCalledWith({ ...original.content, name: "manual-review" }))
  expect(skillDraftsApi.write).toHaveBeenCalledOnce()
})
