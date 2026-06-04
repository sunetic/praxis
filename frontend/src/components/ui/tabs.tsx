import * as React from "react"

import { cn } from "@/lib/utils"

type TabsContextValue = {
  value: string
  onValueChange: (value: string) => void
}

const TabsContext = React.createContext<TabsContextValue | null>(null)

type TabsProps = React.ComponentProps<"div"> & {
  value: string
  onValueChange: (value: string) => void
}

function Tabs({ className, value, onValueChange, ...props }: TabsProps) {
  return (
    <TabsContext.Provider value={{ value, onValueChange }}>
      <div data-slot="tabs" className={cn("w-full", className)} {...props} />
    </TabsContext.Provider>
  )
}

function TabsList({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      role="tablist"
      data-slot="tabs-list"
      className={cn(
        "inline-flex h-9 items-center rounded-md bg-muted p-1 text-muted-foreground",
        className
      )}
      {...props}
    />
  )
}

type TabsTriggerProps = React.ComponentProps<"button"> & {
  value: string
}

function TabsTrigger({ className, value, ...props }: TabsTriggerProps) {
  const context = React.useContext(TabsContext)
  if (!context) {
    throw new Error("TabsTrigger must be used within Tabs")
  }
  const active = context.value === value

  return (
    <button
      type="button"
      role="tab"
      data-slot="tabs-trigger"
      data-state={active ? "active" : "inactive"}
      aria-selected={active}
      className={cn(
        "inline-flex h-7 min-w-[88px] items-center justify-center rounded-sm px-3 text-sm font-medium transition-colors",
        "hover:bg-background/60 hover:text-foreground",
        "data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm",
        "disabled:pointer-events-none disabled:opacity-50",
        className
      )}
      onClick={() => context.onValueChange(value)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger }
