import { useCallback, useMemo } from "react"
import type { ReactNode } from "react"
import {
  useExternalStoreRuntime,
} from "@assistant-ui/react"
import type { AppendMessage, ThreadMessageLike } from "@assistant-ui/react"
import { AssistantRuntimeProvider } from "@assistant-ui/core/react"
import { Loader2, WandSparkles } from "lucide-react"

import { Thread } from "@/components/assistant-ui/thread"
import type { ThreadSuggestion } from "@/components/assistant-ui/thread"
import { Button } from "@/components/ui/button"
import type { ChatHandoff, DataSource, ContentPart as ApiContentPart } from "@/lib/api"
import type { ChatControllerReturn } from "./useChatController"
import { useShellI18n } from "@/i18n/shellI18n"

// ── Types ──────────────────────────────────────────────────────────────

export type ChatThreadViewProps = {
  controller: ChatControllerReturn

  title?: string
  placeholder?: string
  readOnly?: boolean
  readOnlyHint?: string
  suggestions?: ThreadSuggestion[] | string[]
  className?: string
  embedded?: boolean
  showHeader?: boolean
  headerAction?: ReactNode

  enableSaveAsAgent?: boolean
  enableHandoff?: boolean
  enableBatchActions?: boolean

  handoff?: ChatHandoff | null
  onHandoffUsed?: (prompt: string) => void
  datasources?: DataSource[]
  onNavigate?: (url: string) => void
}

// ── Helpers ────────────────────────────────────────────────────────────

type ContentPart =
  | { type: "text"; text: string }
  | { type: "tool-call"; toolCallId: string; toolName: string; args: object; argsText: string; result?: unknown }

function buildContentParts(msg: {
  content?: string | null
  content_parts?: ApiContentPart[] | null
  tool_calls?: Array<{ id: string; name: string; input?: unknown; result?: unknown }> | null
}): ContentPart[] {
  const parts: ContentPart[] = []
  if (msg.content_parts?.length) {
    for (const part of msg.content_parts as ApiContentPart[]) {
      if (part.type === "text" && part.text) {
        parts.push({ type: "text", text: part.text })
      } else if (part.type === "tool_use") {
        parts.push({
          type: "tool-call",
          toolCallId: part.id,
          toolName: part.name,
          args: (part.input as object) ?? {},
          argsText: JSON.stringify(part.input ?? {}, null, 2),
          result: part.result,
        })
      }
    }
  } else {
    if (msg.content) parts.push({ type: "text", text: msg.content })
    for (const tc of msg.tool_calls ?? []) {
      parts.push({
        type: "tool-call",
        toolCallId: tc.id,
        toolName: tc.name,
        args: (tc.input as object) ?? {},
        argsText: JSON.stringify(tc.input ?? {}, null, 2),
        result: tc.result,
      })
    }
  }
  return parts
}

// ── Main component ──────────────────────────────────────────────────────

export function ChatThreadView({
  controller,
  title,
  readOnly = false,
  readOnlyHint,
  suggestions = [],
  className,
  embedded = false,
  showHeader = true,
  headerAction,
  enableSaveAsAgent = true,
  enableHandoff = true,
  enableBatchActions = true,
  handoff,
  onHandoffUsed,
  onNavigate,
}: ChatThreadViewProps) {
  const { t } = useShellI18n()
  const resolvedReadOnlyHint = readOnlyHint || t("chat.readOnly.default")
  const {
    messages: persistedMessages,
    streamingParts,
    streaming,
    currentBatchPendingActions,
    processingActionToken,
    showReuseSuggestion,
    setShowReuseSuggestion,
    saveAgentState,
    savingAgent,
    handleSaveAsAgent,
    sendMessage,
    stopMessage,
    confirmCurrentBatch,
    cancelCurrentBatch,
  } = controller

  // Build the message list for assistant-ui.
  // The streaming message is kept entirely separate from `messages` (it lives in
  // `streamingParts` state) so that its id="streaming" key never collides with a
  // real message id="m-N". This prevents the tapClientLookup index-out-of-bounds
  // error that occurs when a key change causes stale ContentPartRuntime fibers to
  // access a new (shorter) content array during the React commit phase.
  const messages = useMemo<ThreadMessageLike[]>(() => {
    const result: ThreadMessageLike[] = []
    for (const msg of persistedMessages) {
      if (msg.role === "system") continue
      if (msg.role === "user") {
        result.push({
          id: `m-${msg.id}`,
          role: "user",
          content: msg.content || "",
          createdAt: new Date(msg.created_at),
        })
        continue
      }
      const parts = buildContentParts(msg)
      if (parts.length === 0) continue
      const content: ThreadMessageLike["content"] =
        parts.length === 1 && parts[0].type === "text" ? parts[0].text : (parts as ThreadMessageLike["content"])
      result.push({
        id: `m-${msg.id}`,
        role: "assistant",
        content,
        createdAt: new Date(msg.created_at),
      })
    }
    // Append the live streaming message as a separate entry with a stable key.
    // It only exists while streaming===true; once streaming ends it disappears and
    // the real persisted message (with a different id) takes its place — no key
    // collision, no stale fiber access.
    if (streaming && streamingParts.length > 0) {
      // streamingParts uses "tool_use" type (API schema); buildContentParts converts to "tool-call"
      // which is what assistant-ui's fromThreadMessageLike expects.
      const converted = buildContentParts({ content_parts: streamingParts })
      const content: ThreadMessageLike["content"] =
        converted.length === 1 && converted[0].type === "text"
          ? converted[0].text
          : (converted as ThreadMessageLike["content"])
      result.push({
        id: "streaming",
        role: "assistant",
        content,
        createdAt: new Date(),
        status: { type: "running" },
      })
    }
    return result
  }, [persistedMessages, streaming, streamingParts])

  const onNew = useCallback(
    async (msg: AppendMessage) => {
      const text = msg.content
        .filter((p) => p.type === "text")
        .map((p) => (p as { type: "text"; text: string }).text)
        .join("")
        .trim()
      if (text) await sendMessage(text)
    },
    [sendMessage],
  )

  const onCancel = useCallback(async () => { stopMessage() }, [stopMessage])

  const normalizedSuggestions = useMemo(
    () =>
      suggestions.map((s): ThreadSuggestion =>
        typeof s === "string" ? { label: s, prompt: s } : s,
      ),
    [suggestions],
  )

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    isRunning: streaming,
    messages,
    convertMessage: (msg) => msg,
    onNew,
    onCancel,
  })

  const actionsDisabled = streaming || savingAgent || readOnly

  const rootClass = [
    "flex h-full min-h-0 flex-col",
    embedded ? "" : "rounded-xl border border-border bg-card shadow-sm",
    className || "",
  ]
    .filter(Boolean)
    .join(" ")

  return (
    <AssistantRuntimeProvider key={controller.conversationId ?? "new"} runtime={runtime}>
      <div className={rootClass}>
        {/* Optional header */}
        {showHeader && (title || headerAction) ? (
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            {title ? <p className="text-sm font-semibold text-foreground">{title}</p> : <span />}
            {headerAction}
          </div>
        ) : null}

        {/* Handoff card */}
        {enableHandoff && handoff ? (
          <div className="border-b border-border px-4 py-3">
            <div className="flex items-start gap-2 rounded-xl border border-border bg-muted px-3 py-2">
              <WandSparkles className="mt-0.5 size-4 shrink-0 text-primary" />
              <div className="min-w-0 space-y-1.5">
                <p className="text-sm font-medium">{handoff.packet.title}</p>
                <p className="text-xs text-muted-foreground">
                  {t("chat.handoff.from")}{handoff.packet.source.label || handoff.packet.source.page}
                </p>
                {handoff.packet.suggested_prompts.length > 0 ? (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {handoff.packet.suggested_prompts.map((prompt) => (
                      <Button
                        key={prompt}
                        size="sm"
                        variant="outline"
                        disabled={actionsDisabled}
                        onClick={() => onHandoffUsed?.(prompt)}
                        className="h-7 rounded-full px-3 text-xs"
                      >
                        {prompt}
                      </Button>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}

        {/* Main thread — official assistant-ui Thread component */}
        {/* Business overlays (batch actions, save-agent banners) are passed as footerContent
            so they render inside ThreadPrimitive.ViewportFooter, above the composer. */}
        <div className="min-h-0 flex-1 overflow-hidden">
          <Thread
            suggestions={normalizedSuggestions}
            isWaiting={streaming && streamingParts.length === 0}
            footerContent={
              (enableBatchActions && currentBatchPendingActions.length > 0) ||
              (enableSaveAsAgent && showReuseSuggestion) ||
              (enableSaveAsAgent && saveAgentState) ? (
                <div className="flex flex-col gap-2">
                  {enableBatchActions && currentBatchPendingActions.length > 0 ? (
                    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {currentBatchPendingActions.length > 1
                            ? `${currentBatchPendingActions.length} ${t("chat.batch.countMany")}`
                            : t("chat.batch.countOne")}
                        </p>
                        <p className="text-xs text-muted-foreground">{t("chat.batch.hint")}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Button
                          size="sm"
                          onClick={() => void confirmCurrentBatch()}
                          disabled={actionsDisabled || processingActionToken !== null}
                          className="h-7 px-3 text-xs"
                        >
                          {processingActionToken === "__batch_confirm__" ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            t("chat.batch.confirm")
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void cancelCurrentBatch()}
                          disabled={actionsDisabled || processingActionToken !== null}
                          className="h-7 px-3 text-xs"
                        >
                          {processingActionToken === "__batch_cancel__" ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            t("chat.batch.cancel")
                          )}
                        </Button>
                      </div>
                    </div>
                  ) : null}

                  {enableSaveAsAgent && showReuseSuggestion ? (
                    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <WandSparkles className="size-3.5 shrink-0 text-primary" />
                        <p className="text-sm">{t("chat.reuse.prompt")}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={actionsDisabled}
                          onClick={() => void handleSaveAsAgent()}
                          className="h-7 px-3 text-xs"
                        >
                          {savingAgent ? <Loader2 className="size-3.5 animate-spin" /> : t("chat.reuse.save")}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setShowReuseSuggestion(false)}
                          className="h-7 px-3 text-xs"
                        >
                          {t("chat.reuse.ignore")}
                        </Button>
                      </div>
                    </div>
                  ) : null}

                  {enableSaveAsAgent && saveAgentState ? (
                    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0">
                        {saveAgentState.stage === "done" ? (
                          <WandSparkles className="size-3.5 shrink-0 text-primary" />
                        ) : (
                          <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
                        )}
                        <p className="text-sm truncate">{saveAgentState.text}</p>
                      </div>
                      {saveAgentState.stage === "done" ? (
                        <Button
                          size="sm"
                          onClick={() => onNavigate?.(saveAgentState.agentUrl || "/agent")}
                          className="h-7 shrink-0 px-3 text-xs"
                        >
                          {t("chat.reuse.goEdit")}
                        </Button>
                      ) : null}
                      {saveAgentState.stage === "error" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={actionsDisabled}
                          onClick={() => void handleSaveAsAgent()}
                          className="h-7 shrink-0 px-3 text-xs"
                        >
                          {t("chat.reuse.retry")}
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ) : undefined
            }
          />
        </div>

        {/* Read-only notice */}
        {readOnly ? (
          <div className="border-t border-border px-4 py-3">
            <p className="text-xs text-muted-foreground">{resolvedReadOnlyHint}</p>
          </div>
        ) : null}
      </div>
    </AssistantRuntimeProvider>
  )
}
