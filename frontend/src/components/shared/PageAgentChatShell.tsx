import { useEffect } from "react"

import { ChatThreadView } from "@/components/chat/ChatThreadView"
import { useChatController } from "@/components/chat/useChatController"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useShellI18n } from "@/i18n/shellI18n"

import { buildSceneAgentPayload, type PageAgentFocusObject, type PageBusinessAgentAdapter } from "./pageAgentAdapter"

type PageAgentChatShellProps = {
  adapter: PageBusinessAgentAdapter
  datasourceId: number | null
  focusObject?: PageAgentFocusObject
  suggestedPrompt?: string | null
  submitSuggestedPrompt?: boolean
  autoSendSuggestedPrompt?: boolean
  onSuggestedPromptApplied?: () => void
  onJumpToFocusObject?: (focusObject: PageAgentFocusObject) => void
  className?: string
  embeddedInDrawer?: boolean
  freshSessionKey?: string | null
}

const AUTO_PROMPT_TTL_MS = 50
const autoPromptDedupCache = new Map<string, number>()

function buildAutoPromptKey(
  page: string,
  datasourceId: number | null,
  focusObject: PageAgentFocusObject,
  prompt: string
): string {
  return JSON.stringify({
    page,
    datasourceId: datasourceId ?? null,
    focusObject: focusObject ?? null,
    prompt,
  })
}

function shouldSkipAutoPrompt(key: string): boolean {
  const now = Date.now()
  for (const [cacheKey, ts] of autoPromptDedupCache.entries()) {
    if (now - ts > AUTO_PROMPT_TTL_MS) autoPromptDedupCache.delete(cacheKey)
  }
  const last = autoPromptDedupCache.get(key)
  if (typeof last === "number" && now - last <= AUTO_PROMPT_TTL_MS) return true
  autoPromptDedupCache.set(key, now)
  return false
}

export function SceneAgentChatShell({
  adapter,
  datasourceId,
  focusObject = null,
  suggestedPrompt,
  submitSuggestedPrompt,
  autoSendSuggestedPrompt = false,
  onSuggestedPromptApplied,
  onJumpToFocusObject,
  className,
  embeddedInDrawer = false,
  freshSessionKey,
}: PageAgentChatShellProps) {
  const { t } = useShellI18n()
  const title = adapter.title || t("scene.defaultTitle")
  const placeholder = adapter.placeholder || t("scene.defaultPlaceholder")
  const sceneAgentPayload = buildSceneAgentPayload(adapter, focusObject)
  const suggestions = adapter.suggestions || [
    t("scene.suggestion1"),
    t("scene.suggestion2"),
    t("scene.suggestion3"),
    t("scene.suggestion4"),
  ]

  const controller = useChatController({
    title: adapter.conversationTitle || `${adapter.page} · Agent Chat`,
    datasourceId,
    activeSkills: adapter.skills,
    sceneAgentPayload,
    sceneConversationMeta: sceneAgentPayload.key ? { sceneKey: sceneAgentPayload.key } : null,
    fetchOnConversationChange: true,
    freshSessionKey,
  })

  const shouldSubmitSuggestedPrompt = submitSuggestedPrompt ?? autoSendSuggestedPrompt

  useEffect(() => {
    const prompt = String(suggestedPrompt || "").trim()
    if (!prompt) return
    if (shouldSubmitSuggestedPrompt) {
      const dedupeKey = buildAutoPromptKey(adapter.page, datasourceId, focusObject, prompt)
      if (shouldSkipAutoPrompt(dedupeKey)) {
        onSuggestedPromptApplied?.()
        return
      }
      void controller.sendMessage(prompt)
    } else {
      controller.setInput(prompt)
    }
    onSuggestedPromptApplied?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestedPrompt, shouldSubmitSuggestedPrompt, onSuggestedPromptApplied])

  return (
    <div className={`flex min-h-0 flex-col gap-2 ${className ?? ""}`}>
      {controller.activeSkills.length > 0 ? (
        <div className="flex min-w-0 items-center gap-2 rounded-lg border border-border bg-muted/60 px-3 py-1.5">
          <span className="shrink-0 text-xs text-muted-foreground">{t("scene.activeSkills")}</span>
          <div className="flex min-w-0 items-center gap-1">
            {controller.activeSkills.slice(0, 3).map((name) => (
              <Badge key={name} variant="secondary" className="max-w-[200px] truncate text-[10px]">
                {name}
              </Badge>
            ))}
            {controller.activeSkills.length > 3 ? (
              <Badge variant="outline" className="text-[10px]">
                +{controller.activeSkills.length - 3}
              </Badge>
            ) : null}
          </div>
        </div>
      ) : null}
      <ChatThreadView
        controller={controller}
        title={title}
        placeholder={placeholder}
        suggestions={suggestions}
        embedded={embeddedInDrawer}
        showHeader={!embeddedInDrawer}
        enableSaveAsAgent={false}
        enableHandoff={false}
        enableBatchActions={false}
        headerAction={
          focusObject && !embeddedInDrawer ? (
            <Button size="sm" variant="outline" onClick={() => onJumpToFocusObject?.(focusObject)}>
              {t("scene.backToDetail")}
            </Button>
          ) : null
        }
        className="min-h-0 flex-1"
      />
    </div>
  )
}

// Backward-compatible export during migration window.
export const PageAgentChatShell = SceneAgentChatShell
