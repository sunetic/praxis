import { createContext } from "react"

import type { PendingAction } from "@/lib/api"

export type PendingActionContextValue = {
  actionsByToken: Map<string, PendingAction>
  processingToken: string | null
  disabled?: boolean
  onConfirm: (token: string) => Promise<void>
  onCancel: (token: string) => Promise<void>
}

export const PendingActionContext = createContext<PendingActionContextValue | null>(null)

export function getPendingActionToken(result: unknown): string | null {
  if (!result || typeof result !== "object") return null
  const data = (result as Record<string, unknown>).data
  if (!data || typeof data !== "object") return null
  const token = (data as Record<string, unknown>).action_token
  return typeof token === "string" && token.trim() ? token : null
}
