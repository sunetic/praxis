import { createContext, useContext, useEffect, useState } from "react"
import { settingsApi } from "@/lib/api"

export type Edition = "community" | "enterprise"

const EditionContext = createContext<Edition>("community")

export function EditionProvider({ children }: { children: React.ReactNode }) {
  const [edition, setEdition] = useState<Edition>("community")

  useEffect(() => {
    settingsApi.get().then((data) => {
      const e = data.praxis_edition as string
      if (e === "enterprise") setEdition("enterprise")
    }).catch(() => {})
  }, [])

  return <EditionContext.Provider value={edition}>{children}</EditionContext.Provider>
}

export function useEdition(): Edition {
  return useContext(EditionContext)
}

export function useIsEnterprise(): boolean {
  return useContext(EditionContext) === "enterprise"
}
