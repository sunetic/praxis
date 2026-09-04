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
