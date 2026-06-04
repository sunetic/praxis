import * as React from "react"

import { cn } from "@/lib/utils"

type WorkbenchPageProps = {
  actions?: React.ReactNode
  toolbar?: React.ReactNode
  primary: React.ReactNode
  secondary?: React.ReactNode
  className?: string
}

function WorkbenchPage({
  actions,
  toolbar,
  primary,
  secondary,
  className,
}: WorkbenchPageProps) {
  return (
    <div data-slot="workbench-page" className={cn("space-y-6", className)}>
      {actions ? (
        <header data-slot="workbench-header" className="flex items-center justify-end gap-4">
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      ) : null}
      {toolbar ? <div data-slot="workbench-toolbar">{toolbar}</div> : null}
      <div data-slot="workbench-primary">{primary}</div>
      {secondary ? <div data-slot="workbench-secondary">{secondary}</div> : null}
    </div>
  )
}

export { WorkbenchPage }
