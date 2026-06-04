import * as React from "react"

import { cn } from "@/lib/utils"

function FilterToolbar({ className, children, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="filter-toolbar"
      className={cn(
        "flex flex-col gap-3 md:flex-row md:items-center md:justify-between",
        className
      )}
      {...props}
    >
      {children}
    </div>
  )
}

function FilterToolbarGroup({ className, children, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="filter-toolbar-group"
      className={cn("flex flex-col gap-3 md:flex-row md:items-center", className)}
      {...props}
    >
      {children}
    </div>
  )
}

export { FilterToolbar, FilterToolbarGroup }
