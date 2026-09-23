import { useCallback, useEffect, useState } from "react"
import { BrainCircuit, Gauge, ShieldCheck } from "lucide-react"
import { useShellI18n } from "@/i18n/shellI18n"
import { settingsApi } from "@/lib/api"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

type TabId = "llm" | "safety"

// ── LLM tab ───────────────────────────────────────────────────────────────────

function LlmTab() {
  const { t } = useShellI18n()
  const [apiKey, setApiKey] = useState("")
  const [apiKeyChanged, setApiKeyChanged] = useState(false)
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false)
  const [model, setModel] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [contextWindow, setContextWindow] = useState("128000")
  const [compressionThreshold, setCompressionThreshold] = useState("75")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    settingsApi.get().then((data) => {
      setApiKeyConfigured(data.ai_api_key_configured === true)
      setModel(typeof data.ai_model === "string" ? data.ai_model : "")
      setBaseUrl(typeof data.ai_base_url === "string" ? data.ai_base_url : "")
      setContextWindow(String(data.context_window_tokens || 128000))
      setCompressionThreshold(String(data.context_compression_threshold_percent || 75))
      setLoaded(true)
    })
  }, [])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setSaved(false)
    try {
      const payload = {
        ai_model: model.trim(),
        ai_base_url: baseUrl.trim(),
        context_window_tokens: Number(contextWindow),
        context_compression_threshold_percent: Number(compressionThreshold),
        ...(apiKeyChanged ? { ai_api_key: apiKey.trim() } : {}),
      }
      const settings = await settingsApi.patch(payload)
      setApiKey("")
      setApiKeyChanged(false)
      setApiKeyConfigured(settings.ai_api_key_configured === true)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }, [apiKey, apiKeyChanged, model, baseUrl, contextWindow, compressionThreshold])

  const contextWindowNumber = Number(contextWindow)
  const compressionThresholdNumber = Number(compressionThreshold)
  const contextSettingsValid =
    Number.isInteger(contextWindowNumber) &&
    contextWindowNumber >= 8192 &&
    contextWindowNumber <= 2000000 &&
    Number.isInteger(compressionThresholdNumber) &&
    compressionThresholdNumber >= 50 &&
    compressionThresholdNumber <= 95
  const triggerTokens = contextSettingsValid
    ? Math.round(contextWindowNumber * compressionThresholdNumber / 100)
    : 0

  if (!loaded) {
    return (
      <div className="space-y-5 p-5">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-10 w-full max-w-lg" />
        <Skeleton className="h-10 w-full max-w-lg" />
        <Skeleton className="h-10 w-full max-w-lg" />
        <Skeleton className="h-9 w-20" />
      </div>
    )
  }

  return (
    <div className="space-y-5 p-5 max-w-lg">
      <div className="space-y-1.5">
        <label htmlFor="ai-api-key" className="text-sm font-medium">{t("settings.llm.apiKey")}</label>
        <Input
          id="ai-api-key"
          type="password"
          value={apiKey}
          onChange={(event) => {
            setApiKey(event.target.value)
            setApiKeyChanged(true)
          }}
          placeholder={apiKeyConfigured
            ? t("settings.llm.apiKeyConfiguredPlaceholder")
            : "sk-..."}
          autoComplete="off"
        />
        {apiKeyConfigured && (
          <p className="text-xs text-muted-foreground">
            {t("settings.llm.apiKeyConfiguredHint")}
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium">{t("settings.llm.model")}</label>
        <Input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="gpt-4o"
        />
        <p className="text-xs text-muted-foreground">
          {t("settings.llm.modelHint")}
        </p>
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium">Base URL</label>
        <Input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1"
          className="font-mono text-sm"
        />
        <p className="text-xs text-muted-foreground">
          {t("settings.llm.baseUrlHint")}
        </p>
      </div>

      <div className="space-y-4 border-t border-border pt-5">
        <div className="flex items-center gap-2">
          <Gauge className="size-4 text-primary" />
          <div>
            <p className="text-sm font-medium">{t("settings.context.title")}</p>
            <p className="text-xs text-muted-foreground">{t("settings.context.description")}</p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label htmlFor="context-window-tokens" className="text-sm font-medium">
              {t("settings.context.window")}
            </label>
            <Input
              id="context-window-tokens"
              type="number"
              min={8192}
              max={2000000}
              step={1024}
              value={contextWindow}
              onChange={(event) => setContextWindow(event.target.value)}
              aria-describedby="context-window-hint"
            />
            <p id="context-window-hint" className="text-xs text-muted-foreground">
              {t("settings.context.windowHint")}
            </p>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="context-compression-threshold" className="text-sm font-medium">
              {t("settings.context.threshold")}
            </label>
            <div className="relative">
              <Input
                id="context-compression-threshold"
                type="number"
                min={50}
                max={95}
                step={1}
                value={compressionThreshold}
                onChange={(event) => setCompressionThreshold(event.target.value)}
                className="pr-9"
                aria-describedby="context-threshold-hint"
              />
              <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">%</span>
            </div>
            <p id="context-threshold-hint" className="text-xs text-muted-foreground">
              {contextSettingsValid
                ? `${triggerTokens.toLocaleString()} tokens · ${t("settings.context.thresholdHint")}`
                : t("settings.context.invalid")}
            </p>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving || !model.trim() || !contextSettingsValid}
        >
          {saving ? t("settings.saving") : t("settings.save")}
        </Button>
        {saved && <span className="text-sm text-positive">{t("settings.saved")}</span>}
      </div>
    </div>
  )
}

// ── Safety tab ───────────────────────────────────────────────────────────────

function SafetyTab() {
  const { t } = useShellI18n()
  const [allowMutating, setAllowMutating] = useState(false)
  const [savingKey, setSavingKey] = useState<"mutating" | null>(null)
  const [savedKey, setSavedKey] = useState<"mutating" | null>(null)
  const [errorKey, setErrorKey] = useState<"load" | "mutating" | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    settingsApi.get()
      .then((data) => {
        setAllowMutating(data.sql_allow_mutating === true)
        setLoaded(true)
      })
      .catch(() => setErrorKey("load"))
  }, [])

  const handleToggle = useCallback(async (checked: boolean) => {
    setAllowMutating(checked)
    setSavingKey("mutating")
    setSavedKey(null)
    setErrorKey(null)
    try {
      await settingsApi.patch({ sql_allow_mutating: checked })
      setSavedKey("mutating")
      setTimeout(() => setSavedKey(null), 2000)
    } catch {
      setAllowMutating(!checked)
      setErrorKey("mutating")
    } finally {
      setSavingKey(null)
    }
  }, [])

  if (!loaded && errorKey === "load") {
    return (
      <div className="p-5">
        <p className="text-sm text-negative" role="alert">{t("settings.safety.loadError")}</p>
      </div>
    )
  }

  if (!loaded) {
    return (
      <div className="space-y-5 p-5">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-10 w-full max-w-lg" />
      </div>
    )
  }

  return (
    <div className="space-y-5 p-5 max-w-lg">
      <div className="space-y-3">
        <label className="text-sm font-medium">{t("settings.safety.title")}</label>
        <div className="flex items-center gap-3 rounded-lg border bg-muted/30 p-4">
          <Switch
            id="sql-allow-mutating"
            checked={allowMutating}
            onCheckedChange={handleToggle}
            disabled={savingKey !== null}
          />
          <div className="space-y-0.5">
            <label htmlFor="sql-allow-mutating" className="text-sm font-medium cursor-pointer">
              {t("settings.safety.allowMutatingLabel")}
            </label>
            <p className="text-xs text-muted-foreground">
              {t("settings.safety.allowMutatingDesc")}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 pt-1">
          <span className={cn(
            "text-xs font-medium px-2 py-0.5 rounded",
            allowMutating
              ? "bg-warning/15 text-warning"
              : "bg-positive/15 text-positive"
          )}>
            {allowMutating ? t("settings.safety.readWriteBadge") : t("settings.safety.readOnlyBadge")}
          </span>
          {savedKey === "mutating" && <span className="text-sm text-positive">{t("settings.saved")}</span>}
          {errorKey === "mutating" && <span className="text-sm text-negative" role="alert">{t("settings.safety.saveError")}</span>}
        </div>
      </div>


    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function SettingsPage() {
  const { t } = useShellI18n()
  const [activeTab, setActiveTab] = useState<TabId>("llm")

  const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: "llm", label: t("settings.tab.llm"), icon: <BrainCircuit className="size-4" /> },
    { id: "safety", label: t("settings.tab.safety"), icon: <ShieldCheck className="size-4" /> },
  ]

  const primary = (
    <div className="space-y-6">
      <div className="rounded-xl bg-card shadow-sm">
        <div className="border-b border-border px-5 py-3">
          <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as TabId)}>
            <TabsList>
              {TABS.map((tab) => (
                <TabsTrigger key={tab.id} value={tab.id} className="flex items-center gap-1.5">
                  {tab.icon}
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
        {activeTab === "llm" && <LlmTab />}
        {activeTab === "safety" && <SafetyTab />}
      </div>
    </div>
  )

  return <WorkbenchPage primary={primary} />
}
