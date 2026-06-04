import * as React from "react"
import { CalendarRange } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useShellI18n } from "@/i18n/shellI18n"

type QuickRangeOption = {
  label: string
  minutes: number
}

type TimeRangePickerProps = {
  label: string
  quickRanges: QuickRangeOption[]
  disabled?: boolean
  customStart: string
  customEnd: string
  onCustomStartChange: (value: string) => void
  onCustomEndChange: (value: string) => void
  onSelectQuickRange: (minutes: number) => void
  onApplyCustomRange: () => void
}

function TimeRangePicker({
  label,
  quickRanges,
  disabled,
  customStart,
  customEnd,
  onCustomStartChange,
  onCustomEndChange,
  onSelectQuickRange,
  onApplyCustomRange,
}: TimeRangePickerProps) {
  const { t } = useShellI18n()
  const [open, setOpen] = React.useState(false)

  const applyCustom = () => {
    onApplyCustomRange()
    setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" disabled={disabled} className="justify-start">
          <CalendarRange className="size-4" />
          {label}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("shared.timeRange.title")}</DialogTitle>
          <DialogDescription>{t("shared.timeRange.description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <div className="text-sm text-muted-foreground">{t("shared.timeRange.quickLabel")}</div>
            <div className="flex flex-wrap gap-2">
              {quickRanges.map((item) => (
                <Button
                  key={item.minutes}
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    onSelectQuickRange(item.minutes)
                    setOpen(false)
                  }}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-2 text-sm">
              <div className="text-muted-foreground">{t("shared.timeRange.startTime")}</div>
              <Input
                type="datetime-local"
                value={customStart}
                onChange={(event) => onCustomStartChange(event.target.value)}
              />
            </label>
            <label className="space-y-2 text-sm">
              <div className="text-muted-foreground">{t("shared.timeRange.endTime")}</div>
              <Input
                type="datetime-local"
                value={customEnd}
                onChange={(event) => onCustomEndChange(event.target.value)}
              />
            </label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            {t("shared.timeRange.cancel")}
          </Button>
          <Button onClick={applyCustom}>{t("shared.timeRange.apply")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export { TimeRangePicker }
