import { Loader2 } from "lucide-react"
import { motion } from "framer-motion"

import { ThinkingDots } from "@/components/chat/ThinkingDots"

type ThinkingVariant = "chat" | "build"

type ChatThinkingIndicatorProps = {
  text: string
  className?: string
  variant?: ThinkingVariant
}

export function ChatThinkingIndicator({
  text,
  className,
  variant = "chat",
}: ChatThinkingIndicatorProps) {
  if (variant === "chat") {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.35 }}
        className={`flex justify-start ${className || ""}`}
      >
        <div className="max-w-[85%] rounded-xl border border-border bg-muted px-3 py-2">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            <span>{text}</span>
          </div>
        </div>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
      className={`flex justify-start ${className || ""}`}
    >
      <div className="flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
        <ThinkingDots />
        {text}
      </div>
    </motion.div>
  )
}
