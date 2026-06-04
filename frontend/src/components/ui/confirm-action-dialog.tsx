import type { ComponentProps, ReactNode } from "react"
import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useShellI18n } from "@/i18n/shellI18n"

type ConfirmActionDialogProps = {
  open: boolean
  title: string
  description: ReactNode
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
  confirming?: boolean
  confirmDisabled?: boolean
  cancelDisabled?: boolean
  confirmText?: string
  confirmingText?: string
  cancelText?: string
  confirmVariant?: ComponentProps<typeof Button>["variant"]
}

export function ConfirmActionDialog({
  open,
  title,
  description,
  onOpenChange,
  onConfirm,
  confirming = false,
  confirmDisabled = false,
  cancelDisabled = false,
  confirmText: confirmTextProp,
  confirmingText,
  cancelText: cancelTextProp,
  confirmVariant = "destructive",
}: ConfirmActionDialogProps) {
  const { t } = useShellI18n()
  const confirmText = confirmTextProp ?? t("ui.confirmDialog.confirm")
  const cancelText = cancelTextProp ?? t("ui.confirmDialog.cancel")
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={confirming || cancelDisabled}>
            {cancelText}
          </Button>
          <Button variant={confirmVariant} onClick={onConfirm} disabled={confirming || confirmDisabled}>
            {confirming ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
            {confirming ? (confirmingText ?? confirmText) : confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
