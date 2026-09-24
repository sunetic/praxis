import type { ShellCopyKey, ShellTranslatorFn } from "@/i18n/shellI18n"
import type { Agent } from "@/lib/api"

export const PAGE_SIZE = 10

export const RUN_DS_SELECTION_STORAGE_KEY = "agent-run-datasource-selection:v1"

export type EditingAgent = Partial<Agent>

export type GuidedField = "goal" | "workflow" | "constraints" | "tools" | "skills"

type GuidedQuestion = {
  field: GuidedField
  prompt: ShellCopyKey
}

export type GuidedMessage = {
  id: string
  role: "assistant" | "user"
  content: string
}

export const GUIDED_QUESTIONS: GuidedQuestion[] = [
  { field: "goal", prompt: "agents.guided.q.goal" },
  { field: "workflow", prompt: "agents.guided.q.workflow" },
  { field: "constraints", prompt: "agents.guided.q.constraints" },
  { field: "tools", prompt: "agents.guided.q.tools" },
  { field: "skills", prompt: "agents.guided.q.skills" },
]

export function normalizeCsvItems(raw: string): string[] {
  return raw
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function normalizeRunDatasourceSelection(raw: unknown): Record<number, number[]> {
  if (!raw || typeof raw !== "object") return {}
  const result: Record<number, number[]> = {}
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const agentId = Number(key)
    if (!Number.isInteger(agentId) || agentId <= 0) continue
    if (!Array.isArray(value)) continue
    const ids: number[] = []
    for (const item of value) {
      const datasourceId = Number(item)
      if (!Number.isInteger(datasourceId) || datasourceId <= 0) continue
      if (ids.includes(datasourceId)) continue
      ids.push(datasourceId)
    }
    result[agentId] = ids
  }
  return result
}

export function deriveAgentName(seed: string, fallbackName: string): string {
  const cleaned = seed.replace(/[。！？.!?]/g, " ").trim()
  const short = cleaned.length > 20 ? cleaned.slice(0, 20) : cleaned
  if (!short) return fallbackName
  return short.endsWith("Agent") ? short : `${short} Agent`
}

export function buildPromptFromGuide(answers: Partial<Record<GuidedField, string>>, t: ShellTranslatorFn): string {
  const goal = (answers.goal || "").trim()
  const workflow = (answers.workflow || "").trim()
  const constraints = (answers.constraints || "").trim()
  return [
    t("agents.promptRole"),
    "",
    t("agents.promptGoalHeading"),
    goal || t("agents.promptGoalDefault"),
    "",
    t("agents.promptWorkflowHeading"),
    workflow || t("agents.promptWorkflowDefault"),
    "",
    t("agents.promptConstraintsHeading"),
    constraints || t("agents.promptConstraintsDefault"),
  ].join("\n")
}

export function buildDraftFromConversation(messages: { role: string; content: string }[], t: ShellTranslatorFn): EditingAgent {
  const userMessages = messages
    .filter((msg) => msg.role === "user")
    .map((msg) => (msg.content || "").trim())
    .filter(Boolean)
  const assistantMessages = messages
    .filter((msg) => msg.role === "assistant")
    .map((msg) => (msg.content || "").trim())
    .filter(Boolean)

  const latestUser = userMessages[userMessages.length - 1] || t("agents.handoff.dbAnalysisTask")
  const examples = userMessages.slice(-3).map((item, index) => `${index + 1}. ${item}`)
  const latestAssistant = assistantMessages[assistantMessages.length - 1] || ""

  const promptLines = [
    t("agents.handoff.promptRole"),
    "",
    t("agents.handoff.intentSummary"),
    ...examples,
    "",
    t("agents.handoff.replyStyle"),
    t("agents.handoff.replyStyleContent"),
  ]

  if (latestAssistant) {
    promptLines.push("", t("agents.handoff.refAnswerStyle"), latestAssistant.slice(0, 600))
  }

  return {
    name: deriveAgentName(latestUser, t("agents.newAgentFallback")),
    description: `${t("agents.handoff.descPrefix")}${latestUser.slice(0, 64)}`,
    prompt: promptLines.join("\n"),
    tools: [],
    skills: [],
  }
}
