import { useState } from "react"
import { Check, ClipboardCopy } from "lucide-react"
import { cn } from "@/lib/utils"
import { useShellI18n } from "@/i18n/shellI18n"

type CodeBlockProps = {
  content: string
  label?: string
  maxHeight?: string
  className?: string
}

export function CodeBlock({ content, label, maxHeight = "320px", className }: CodeBlockProps) {
  const { t } = useShellI18n()
  const [copied, setCopied] = useState(false)

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-[#2a2a3a] bg-[#1e1e2e] px-4 py-2.5 rounded-t-lg">
        {label ? (
          <span className="text-xs font-medium text-[#cdd6f4]">{label}</span>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={copyCode}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] text-[#a6adc8] transition-colors hover:bg-[#313244] hover:text-[#cdd6f4]"
          aria-label={t("shared.codeBlock.copyAria")}
        >
          {copied ? <Check className="size-3" /> : <ClipboardCopy className="size-3" />}
          {copied ? t("shared.codeBlock.copied") : t("shared.codeBlock.copy")}
        </button>
      </div>
      <div className="overflow-x-auto bg-[#1e1e2e] rounded-b-lg p-4">
        <pre
          style={{ maxHeight }}
          className={cn("overflow-auto whitespace-pre-wrap break-all text-[13px] leading-relaxed font-mono text-[#cdd6f4]", className)}
        >
          {content}
        </pre>
      </div>
    </div>
  )
}
