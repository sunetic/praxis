import type { SceneAgentPayload } from "@/lib/api"

export type SceneAgentFocusObject = Record<string, unknown> | null

export type SceneBusinessAgentAdapter = {
  page: string
  profile?: string
  sceneKey?: string
  title?: string
  placeholder?: string
  conversationTitle?: string
  suggestions?: string[]
  tools?: string[]
  skills?: string[]
  buildContext?: (focusObject: SceneAgentFocusObject) => Record<string, unknown>
}

function inferSceneKey(adapter: SceneBusinessAgentAdapter): string {
  if (adapter.sceneKey && adapter.sceneKey.trim()) return adapter.sceneKey.trim()
  if (adapter.profile === "page_chat_agent") return "page_build"
  if (adapter.page === "page-console") return "page_build"
  if (adapter.profile === "stats_analysis_agent") return "stats_analysis"
  if (adapter.page === "stats-analysis") return "stats_analysis"
  return ""
}

export function buildSceneAgentPayload(
  adapter: SceneBusinessAgentAdapter,
  focusObject: SceneAgentFocusObject
): SceneAgentPayload {
  return {
    key: inferSceneKey(adapter),
    context: adapter.buildContext ? adapter.buildContext(focusObject) : {},
    focus_object: focusObject,
    tools: adapter.tools,
    skills: adapter.skills,
  }
}

// Backward-compatible aliases during migration window.
export type PageAgentFocusObject = SceneAgentFocusObject
export type PageBusinessAgentAdapter = SceneBusinessAgentAdapter
