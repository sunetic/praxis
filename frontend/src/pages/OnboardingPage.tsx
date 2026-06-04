import { useState, useCallback, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { CheckCircle2, ChevronRight, Cpu, Globe, KeyRound, Loader2, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { onboardingApi, type LlmConfig } from "@/lib/api"
import { toast } from "sonner"
import { useShellI18n } from "@/i18n/shellI18n"

// ── Provider presets ──────────────────────────────────────────────────────────

type ProviderPreset = {
  id: string
  labelKey?: string
  label?: string
  baseUrl: string
  models: string[]
  placeholder: string
}

const PROVIDERS: ProviderPreset[] = [
  {
    id: "openai",
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    placeholder: "sk-...",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    models: ["deepseek-chat", "deepseek-reasoner"],
    placeholder: "sk-...",
  },
  {
    id: "qwen",
    labelKey: "onboarding.provider.qwen",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
    placeholder: "sk-...",
  },
  {
    id: "custom",
    labelKey: "onboarding.provider.custom",
    baseUrl: "",
    models: [],
    placeholder: "API Key",
  },
]

// ── Step indicator ────────────────────────────────────────────────────────────

type StepDef = { id: number; labelKey: string }
const STEPS: StepDef[] = [
  { id: 1, labelKey: "onboarding.step.welcome" },
  { id: 2, labelKey: "onboarding.step.llmConfig" },
  { id: 3, labelKey: "onboarding.step.done" },
]

function StepIndicator({ current }: { current: number }) {
  const { t } = useShellI18n()
  return (
    <div className="flex items-center gap-3">
      {STEPS.map((step, idx) => {
        const done = step.id < current
        const active = step.id === current
        return (
          <div key={step.id} className="flex items-center gap-3">
            <div
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold transition-colors",
                done && "bg-primary text-primary-foreground",
                active && "bg-primary text-primary-foreground ring-4 ring-primary/20",
                !done && !active && "bg-muted text-muted-foreground",
              )}
            >
              {done ? <CheckCircle2 className="h-4 w-4" /> : step.id}
            </div>
            <span
              className={cn(
                "text-sm",
                active ? "font-medium text-foreground" : "text-muted-foreground",
              )}
            >
              {t(step.labelKey as any)}
            </span>
            {idx < STEPS.length - 1 && (
              <ChevronRight className="h-4 w-4 text-muted-foreground/40 mx-1" />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Step 1: Welcome ───────────────────────────────────────────────────────────

function WelcomeStep({ onNext }: { onNext: () => void }) {
  const { t, locale, toggleLocale } = useShellI18n()
  const features = useMemo(() => [
    { icon: "🤖", titleKey: "onboarding.welcome.featureChat" as const, descKey: "onboarding.welcome.featureChatDesc" as const },
    { icon: "📊", titleKey: "onboarding.welcome.featureSql" as const, descKey: "onboarding.welcome.featureSqlDesc" as const },
    { icon: "⚡", titleKey: "onboarding.welcome.featureAuto" as const, descKey: "onboarding.welcome.featureAutoDesc" as const },
  ], [])
  return (
    <div className="flex flex-col items-center text-center gap-8">
      <div className="flex w-full justify-end -mb-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleLocale}
          className="gap-1.5 text-muted-foreground"
          aria-label={t("topbar.locale.switchAria")}
        >
          <Globe className="size-4" />
          {locale === "zh-CN" ? t("topbar.locale.switchToEn") : t("topbar.locale.switchToZh")}
        </Button>
      </div>
      <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-primary/10">
        <Sparkles className="h-10 w-10 text-primary" />
      </div>
      <div className="space-y-3">
        <h2 className="text-3xl font-bold tracking-tight">{t("onboarding.welcome.title")}</h2>
        <p className="text-muted-foreground text-base max-w-sm leading-relaxed">
          {t("onboarding.welcome.desc")}
        </p>
      </div>
      <div className="grid grid-cols-3 gap-4 w-full mt-2">
        {features.map((item) => (
          <div
            key={item.titleKey}
            className="rounded-xl border bg-muted/30 p-4 text-left space-y-2"
          >
            <div className="text-2xl">{item.icon}</div>
            <div className="text-sm font-semibold">{t(item.titleKey)}</div>
            <div className="text-xs text-muted-foreground leading-relaxed">{t(item.descKey)}</div>
          </div>
        ))}
      </div>
      <Button size="lg" className="w-full h-12 text-base mt-2" onClick={onNext}>
        {t("onboarding.welcome.startBtn")}
        <ChevronRight className="ml-2 h-5 w-5" />
      </Button>
    </div>
  )
}

// ── Step 2: LLM Config ────────────────────────────────────────────────────────

type LlmFormState = {
  provider: string
  apiKey: string
  model: string
  baseUrl: string
  customModel: string
}

function LlmConfigStep({
  onComplete,
}: {
  onComplete: (config: LlmConfig) => Promise<void>
}) {
  const { t } = useShellI18n()
  const [form, setForm] = useState<LlmFormState>({
    provider: "openai",
    apiKey: "",
    model: "gpt-4o",
    baseUrl: PROVIDERS[0].baseUrl,
    customModel: "",
  })
  const [saving, setSaving] = useState(false)

  const preset = PROVIDERS.find((p) => p.id === form.provider) ?? PROVIDERS[0]
  const isCustom = form.provider === "custom"
  const effectiveModel = isCustom ? form.customModel : form.model

  const resolveLabel = useCallback((p: ProviderPreset) => {
    if (p.labelKey) return t(p.labelKey as any)
    return p.label ?? p.id
  }, [t])

  const handleProviderChange = (id: string) => {
    const p = PROVIDERS.find((pr) => pr.id === id) ?? PROVIDERS[0]
    setForm((f) => ({
      ...f,
      provider: id,
      baseUrl: p.baseUrl,
      model: p.models[0] ?? "",
      customModel: "",
    }))
  }

  const canSubmit = form.apiKey.trim() && effectiveModel.trim() && (isCustom ? form.baseUrl.trim() : true)

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSaving(true)
    try {
      await onComplete({
        llm_provider: form.provider,
        llm_api_key: form.apiKey.trim(),
        llm_model: effectiveModel.trim(),
        llm_base_url: form.baseUrl.trim() || undefined,
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-6 w-full">
      <div className="space-y-2">
        <h2 className="text-2xl font-bold tracking-tight">{t("onboarding.llm.title")}</h2>
        <p className="text-muted-foreground">
          {t("onboarding.llm.desc")}
        </p>
      </div>

      {/* Provider */}
      <div className="space-y-2">
        <label className="text-sm font-medium">{t("onboarding.llm.providerLabel")}</label>
        <Select value={form.provider} onValueChange={handleProviderChange}>
          <SelectTrigger className="h-11">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PROVIDERS.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {resolveLabel(p)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* API Key */}
      <div className="space-y-2">
        <label className="text-sm font-medium flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-muted-foreground" />
          API Key
        </label>
        <Input
          type="password"
          className="h-11"
          placeholder={preset.placeholder}
          value={form.apiKey}
          onChange={(e) => setForm((f) => ({ ...f, apiKey: e.target.value }))}
          autoComplete="off"
        />
      </div>

      {/* Model */}
      <div className="space-y-2">
        <label className="text-sm font-medium flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          {t("onboarding.llm.modelLabel")}
        </label>
        {isCustom ? (
          <Input
            className="h-11"
            placeholder={t("onboarding.llm.modelPlaceholder")}
            value={form.customModel}
            onChange={(e) => setForm((f) => ({ ...f, customModel: e.target.value }))}
          />
        ) : (
          <Select
            value={form.model}
            onValueChange={(v) => setForm((f) => ({ ...f, model: v }))}
          >
            <SelectTrigger className="h-11">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {preset.models.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* Base URL */}
      {isCustom ? (
        <div className="space-y-2">
          <label className="text-sm font-medium">Base URL</label>
          <Input
            className="h-11"
            placeholder="https://your-api-endpoint/v1"
            value={form.baseUrl}
            onChange={(e) => setForm((f) => ({ ...f, baseUrl: e.target.value }))}
          />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground -mt-2">
          Base URL: <span className="font-mono">{preset.baseUrl}</span>
        </p>
      )}

      <Button
        size="lg"
        className="w-full h-12 text-base mt-2"
        disabled={!canSubmit || saving}
        onClick={handleSubmit}
      >
        {saving ? (
          <>
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            {t("onboarding.llm.saving")}
          </>
        ) : (
          <>
            {t("onboarding.llm.submitBtn")}
            <ChevronRight className="ml-2 h-5 w-5" />
          </>
        )}
      </Button>
    </div>
  )
}

// ── Step 3: Done ──────────────────────────────────────────────────────────────

function DoneStep({ onEnter }: { onEnter: () => void }) {
  const { t } = useShellI18n()
  return (
    <div className="flex flex-col items-center text-center gap-8">
      <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-green-50">
        <CheckCircle2 className="h-10 w-10 text-green-500" />
      </div>
      <div className="space-y-3">
        <h2 className="text-3xl font-bold tracking-tight">{t("onboarding.done.title")}</h2>
        <p className="text-muted-foreground text-base max-w-sm leading-relaxed">
          {t("onboarding.done.desc")}
        </p>
      </div>
      <Button size="lg" className="w-full h-12 text-base" onClick={onEnter}>
        {t("onboarding.done.enterBtn")}
        <ChevronRight className="ml-2 h-5 w-5" />
      </Button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function OnboardingPage() {
  const navigate = useNavigate()
  const { t } = useShellI18n()
  const [step, setStep] = useState(1)

  const handleLlmComplete = useCallback(async (config: LlmConfig) => {
    try {
      await onboardingApi.complete(config)
      setStep(3)
    } catch {
      toast.error(t("onboarding.toast.saveFailed"))
    }
  }, [t])

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center p-8">
      <div className="w-full max-w-lg">
        {/* Logo */}
        <div className="mb-10 flex flex-col items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground font-bold text-xl select-none">
            P
          </div>
          <span className="text-sm font-semibold tracking-widest text-foreground/60 uppercase">Praxis</span>
        </div>

        {/* Step indicator */}
        <div className="mb-8 flex justify-center">
          <StepIndicator current={step} />
        </div>

        {/* Step content */}
        <div className="rounded-2xl border bg-card shadow-lg p-10">
          {step === 1 && <WelcomeStep onNext={() => setStep(2)} />}
          {step === 2 && <LlmConfigStep onComplete={handleLlmComplete} />}
          {step === 3 && <DoneStep onEnter={() => navigate("/chat", { replace: true })} />}
        </div>

        <p className="mt-8 text-center text-xs text-muted-foreground">
          {t("onboarding.privacy")}
        </p>
      </div>
    </div>
  )
}
