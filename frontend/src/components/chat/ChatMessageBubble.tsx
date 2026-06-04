import { motion } from "framer-motion"
import type { ReactNode } from "react"

export type ChatBubbleRole = "user" | "assistant" | "status"
type BubbleVariant = "chat" | "build"

type ChatMessageBubbleProps = {
  role: ChatBubbleRole
  children: ReactNode
  assistantExtraClassName?: string
  className?: string
  variant?: BubbleVariant
  agentName?: string
}

export function ChatMessageBubble({
  role,
  children,
  assistantExtraClassName,
  className,
  variant = "chat",
  agentName,
}: ChatMessageBubbleProps) {
  const alignClass = role === "user" ? "justify-end" : "justify-start"
  const wrapperMaxWidthClass =
    variant === "chat"
      ? "max-w-[85%]"
      : role === "user"
        ? "max-w-[90%]"
        : "max-w-[92%]"
  const bubbleBaseClass =
    variant === "chat"
      ? role === "user"
        ? "rounded-xl bg-primary px-3 py-2 text-sm text-primary-foreground"
        : role === "status"
          ? "rounded-xl border border-border bg-muted px-3 py-2 text-sm text-muted-foreground"
          : `rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground ${assistantExtraClassName || ""}`
      : role === "user"
        ? "whitespace-pre-wrap rounded-xl rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground"
        : role === "status"
          ? "whitespace-pre-wrap rounded-lg border border-border bg-muted px-3 py-2 text-xs text-muted-foreground"
          : `whitespace-pre-wrap rounded-xl rounded-bl-sm border border-border bg-card px-3 py-2 text-sm text-foreground shadow-sm ${assistantExtraClassName || ""}`

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className={`flex ${alignClass} ${className || ""}`}
    >
      <div className={`flex flex-col min-w-0 ${wrapperMaxWidthClass}`}>
        {agentName && role !== "user" ? (
          <span className="mb-1 ml-1 text-[11px] font-medium text-muted-foreground">{agentName}</span>
        ) : null}
        <div className={bubbleBaseClass}>{children}</div>
      </div>
    </motion.div>
  )
}
