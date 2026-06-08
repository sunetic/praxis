import { useCallback, useEffect, useState } from "react"
import { BrainCircuit, ShieldCheck, Wrench } from "lucide-react"
import { useShellI18n } from "@/i18n/shellI18n"
import { settingsApi } from "@/lib/api"
import { WorkbenchPage } from "@/components/shared/WorkbenchPage"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

type EngineChoice = "pi_lite" | "external_cli"
type TabId = "llm" | "build" | "safety"

// ── LLM tab ───────────────────────────────────────────────────────────────────

function LlmTab() {
  const { t } = useShellI18n()
  const [apiKey, setApiKey] = useState("")
  const [model, setModel] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    settingsApi.get().then((data) => {
      setApiKey(typeof data.ai_api_key === "string" ? data.ai_api_key : "")
      setModel(typeof data.ai_model === "string" ? data.ai_model : "")
      setBaseUrl(typeof data.ai_base_url === "string" ? data.ai_base_url : "")
      setLoaded(true)
    })
  }, [])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setSaved(false)
    try {
      await settingsApi.patch({
        ai_api_key: apiKey.trim(),
        ai_model: model.trim(),
        ai_base_url: baseUrl.trim(),
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }, [apiKey, model, baseUrl])

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
        <label className="text-sm font-medium">API Key</label>
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-..."
          autoComplete="off"
        />
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

      <div className="flex items-center gap-3 pt-1">
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving || !apiKey.trim() || !model.trim()}
        >
          {saving ? t("settings.saving") : t("settings.save")}
        </Button>
        {saved && <span className="text-sm text-positive">{t("settings.saved")}</span>}
      </div>
    </div>
  )
}

// ── Build engine tab ──────────────────────────────────────────────────────────

function BuildTab() {
  const { t } = useShellI18n()
  const [engine, setEngine] = useState<EngineChoice>("pi_lite")
  const [command, setCommand] = useState("")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{
    ok: boolean; message: string
    suggested_command?: string; flags_added?: string[]; env_issues?: string[]
  } | null>(null)

  useEffect(() => {
    settingsApi.get().then((data) => {
      setEngine((data.build_engine as EngineChoice) || "pi_lite")
      setCommand(data.external_cli_command || "")
      setLoaded(true)
    })
  }, [])

  const handleTest = useCallback(async () => {
    const cmd = command.trim()
    if (!cmd) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = await settingsApi.testEngine(cmd)
      setTestResult(result)
      if (result.ok && result.suggested_command) setCommand(result.suggested_command)
    } catch {
      setTestResult({ ok: false, message: t("settings.engine.testError") })
    } finally {
      setTesting(false)
    }
  }, [command, t])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setSaved(false)
    try {
      await settingsApi.patch({ build_engine: engine, external_cli_command: command })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }, [engine, command])

  const commandEmpty = engine === "external_cli" && !command.trim()
  const testRequired = engine === "external_cli" && !testResult?.ok

  if (!loaded) {
    return (
      <div className="space-y-5 p-5">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-9 w-20" />
      </div>
    )
  }

  return (
    <div className="space-y-5 p-5 max-w-lg">
      <div className="space-y-1.5">
        <label className="text-sm font-medium">{t("settings.llm.engineLabel")}</label>
        <Select
          value={engine}
          onValueChange={(v) => { setEngine(v as EngineChoice); setTestResult(null) }}
        >
          <SelectTrigger className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pi_lite">{t("settings.engine.builtin")}</SelectItem>
            <SelectItem value="external_cli">{t("settings.engine.external")}</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          {engine === "pi_lite" ? t("settings.engine.builtinDesc") : t("settings.engine.externalDesc")}
        </p>
      </div>

      {engine === "external_cli" && (
        <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("settings.engine.command")}</label>
            <Input
              value={command}
              onChange={(e) => { setCommand(e.target.value); setTestResult(null) }}
              placeholder={t("settings.engine.commandPlaceholder")}
              className={cn("font-mono text-sm", commandEmpty && "border-destructive")}
            />
            <p className="text-xs text-muted-foreground">{t("settings.engine.commandHint")}</p>
            {commandEmpty && (
              <p className="text-xs text-destructive">{t("settings.engine.commandRequired")}</p>
            )}
          </div>

          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTest}
              disabled={testing || !command.trim()}
            >
              {testing ? t("settings.engine.testing") : t("settings.engine.testBtn")}
            </Button>
            {testResult && (
              <span className={cn("text-sm", testResult.ok ? "text-positive" : "text-destructive")}>
                {testResult.ok ? t("settings.engine.testOk") : testResult.message}
              </span>
            )}
          </div>

          {testResult?.ok && testResult.flags_added && testResult.flags_added.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {t("settings.engine.flagsDiscovered")}{" "}
              <code className="rounded bg-muted px-1 py-0.5">{testResult.flags_added.join(" ")}</code>
            </p>
          )}
          {testResult?.env_issues && testResult.env_issues.length > 0 && (
            <div className="space-y-1">
              {testResult.env_issues.map((issue, i) => (
                <p key={i} className="text-xs text-destructive">{issue}</p>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex items-center gap-3 pt-1">
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving || commandEmpty || testRequired}
        >
          {saving ? t("settings.saving") : t("settings.save")}
        </Button>
        {saved && <span className="text-sm text-positive">{t("settings.saved")}</span>}
        {testRequired && !commandEmpty && (
          <span className="text-xs text-muted-foreground">{t("settings.engine.testRequired")}</span>
        )}
      </div>
    </div>
  )
}

// ── Safety tab ───────────────────────────────────────────────────────────────

function SafetyTab() {
  const { t } = useShellI18n()
  const [allowMutating, setAllowMutating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    settingsApi.get().then((data) => {
      setAllowMutating(data.sql_allow_mutating === true)
      setLoaded(true)
    })
  }, [])

  const handleToggle = useCallback(async (checked: boolean) => {
    setAllowMutating(checked)
    setSaving(true)
    setSaved(false)
    try {
      await settingsApi.patch({ sql_allow_mutating: checked })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }, [])

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
            disabled={saving}
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
              ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300"
              : "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300"
          )}>
            {allowMutating ? t("settings.safety.readWriteBadge") : t("settings.safety.readOnlyBadge")}
          </span>
          {saved && <span className="text-sm text-positive">{t("settings.saved")}</span>}
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
    { id: "build", label: t("settings.tab.build"), icon: <Wrench className="size-4" /> },
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
        {activeTab === "build" && <BuildTab />}
        {activeTab === "safety" && <SafetyTab />}
      </div>
    </div>
  )

  return <WorkbenchPage primary={primary} />
}
