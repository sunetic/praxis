import { useEffect, useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { CheckCircle2, Circle, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useShellI18n } from "@/i18n/shellI18n"
import { channelsApi, type Channel, type ChannelInput, type ChannelMessageType, type ChannelProvider } from "@/lib/api"

type SecurityMode = "keyword" | "sign" | "ip"
type WizardStep = 1 | 2 | 3

function extractAccessToken(webhookUrl: string): string {
  try {
    const parsed = new URL(webhookUrl.trim())
    return parsed.searchParams.get("access_token") || ""
  } catch {
    return ""
  }
}

function maskToken(token: string): string {
  if (!token) return "-"
  if (token.length <= 6) return token
  return `${token.slice(0, 3)}***${token.slice(-3)}`
}

export function ChannelConsolePage() {
  const navigate = useNavigate()
  const { provider } = useParams<{ provider?: string }>()
  const { t } = useShellI18n()

  const CHANNEL_CARDS: Array<{
    provider: ChannelProvider
    title: string
    description: string
    available: boolean
  }> = [
    { provider: "dingtalk", title: t("channel.provider.dingtalk"), description: t("channel.provider.dingtalkDesc"), available: true },
    { provider: "slack", title: t("channel.provider.slack"), description: t("channel.provider.slackDesc"), available: true },
    { provider: "telegram", title: t("channel.provider.telegram"), description: t("channel.provider.telegramDesc"), available: true },
    { provider: "feishu", title: t("channel.provider.feishu"), description: t("channel.provider.feishuDesc"), available: false },
    { provider: "wechat", title: t("channel.provider.wechat"), description: t("channel.provider.wechatDesc"), available: false },
  ]

  const currentProvider = (provider || "").toLowerCase()
  const isDingTalk = currentProvider === "dingtalk"
  const isSlack = currentProvider === "slack"
  const isTelegram = currentProvider === "telegram"
  const isSupported = isDingTalk || isSlack || isTelegram

  // ── shared state ──
  const [savedCount, setSavedCount] = useState(0)
  const [channelName, setChannelName] = useState(t("channel.defaultBotName"))
  const [existingChannel, setExistingChannel] = useState<Channel | null>(null)
  const [saving, setSaving] = useState(false)

  // ── DingTalk state ──
  const [step, setStep] = useState<WizardStep>(1)
  const [webhookUrl, setWebhookUrl] = useState("")
  const [securityMode, setSecurityMode] = useState<SecurityMode>("sign")
  const [keyword, setKeyword] = useState(t("channel.defaultKeyword"))
  const [secret, setSecret] = useState("")
  const [ipWhitelist, setIpWhitelist] = useState("")
  const [messageType, setMessageType] = useState<ChannelMessageType>("markdown")
  const [messageTitle, setMessageTitle] = useState(t("channel.defaultMsgTitle"))
  const [messageBody, setMessageBody] = useState(t("channel.defaultMsgBody"))
  const [atAll, setAtAll] = useState(false)
  const [atUserIds, setAtUserIds] = useState("")

  // ── Slack state ──
  const [slackStep, setSlackStep] = useState<1 | 2>(1)
  const [slackWebhookUrl, setSlackWebhookUrl] = useState("")
  const [slackUsername, setSlackUsername] = useState("")
  const [slackIconEmoji, setSlackIconEmoji] = useState("")
  const [slackChannel, setSlackChannel] = useState("")
  const [slackMessageBody, setSlackMessageBody] = useState("Praxis test message")

  // ── Telegram state ──
  const [telegramStep, setTelegramStep] = useState<1 | 2>(1)
  const [telegramBotToken, setTelegramBotToken] = useState("")
  const [telegramChatId, setTelegramChatId] = useState("")
  const [telegramParseMode, setTelegramParseMode] = useState<"Markdown" | "HTML">("Markdown")
  const [telegramDisableNotification, setTelegramDisableNotification] = useState(false)
  const [telegramMessageBody, setTelegramMessageBody] = useState("Praxis test message")

  // ── DingTalk derived state ──
  const DINGTALK_STEPS: Array<{ id: WizardStep; label: string }> = [
    { id: 1, label: t("channel.step.basicInfo") },
    { id: 2, label: t("channel.step.security") },
    { id: 3, label: t("channel.step.messageTemplate") },
  ]

  const DINGTALK_REFERENCE_LINKS = [
    { title: t("channel.ref.robotAccess"), url: "https://open.dingtalk.com/document/robots/custom-robot-access", updatedAt: "2025-05-12" },
    { title: t("channel.ref.robotSecurity"), url: "https://open.dingtalk.com/document/orgapp/customize-robot-security-settings", updatedAt: "2024-02-23" },
    { title: t("channel.ref.robotGroupMsg"), url: "https://open.dingtalk.com/document/orgapp/custom-bot-to-send-group-chat-messages", updatedAt: "2025-05-30" },
    { title: t("channel.ref.robotMsgType"), url: "https://open.dingtalk.com/document/orgapp/custom-bot-send-message-type", updatedAt: "2025-06-05" },
  ]

  const SLACK_REFERENCE_LINKS = [
    { title: t("channel.slack.ref.webhookSetup"), url: "https://api.slack.com/messaging/webhooks", updatedAt: "2025-01-15" },
    { title: t("channel.slack.ref.messageFormat"), url: "https://api.slack.com/reference/surfaces/formatting", updatedAt: "2025-03-20" },
  ]

  const TELEGRAM_REFERENCE_LINKS = [
    { title: t("channel.telegram.ref.botfather"), url: "https://core.telegram.org/bots/tutorial", updatedAt: "2025-02-10" },
    { title: t("channel.telegram.ref.sendMessage"), url: "https://core.telegram.org/bots/api#sendmessage", updatedAt: "2025-04-01" },
    { title: t("channel.telegram.ref.getChatId"), url: "https://core.telegram.org/bots/faq#how-do-i-get-my-chat-id", updatedAt: "2025-01-08" },
  ]

  const hydrateDingTalkDraft = (channel: Channel) => {
    const config = (channel.config || {}) as Record<string, any>
    const security = (config.security || {}) as Record<string, any>
    const template = (config.template || {}) as Record<string, any>
    setChannelName(channel.name || t("channel.defaultBotName"))
    setWebhookUrl(config.webhook_url || "")
    setSecurityMode((security.mode as SecurityMode) || "sign")
    setKeyword(security.keyword || "")
    setSecret(security.secret || "")
    setIpWhitelist(Array.isArray(security.ip_whitelist) ? security.ip_whitelist.join("\n") : "")
    setMessageType((template.type as ChannelMessageType) || "markdown")
    setMessageTitle(template.title || t("channel.defaultMsgTitle"))
    setMessageBody(template.body || t("channel.defaultMsgBody"))
    setAtAll(Boolean(template.at_all))
    setAtUserIds(Array.isArray(template.at_user_ids) ? template.at_user_ids.join(",") : "")
  }

  const hydrateSlackDraft = (channel: Channel) => {
    const config = (channel.config || {}) as Record<string, any>
    const template = (config.template || {}) as Record<string, any>
    setChannelName(channel.name || "Slack Bot")
    setSlackWebhookUrl(config.webhook_url || "")
    setSlackUsername(template.username || "")
    setSlackIconEmoji(template.icon_emoji || "")
    setSlackChannel(template.channel || "")
    setSlackMessageBody(template.body || "Praxis test message")
  }

  const hydrateTelegramDraft = (channel: Channel) => {
    const config = (channel.config || {}) as Record<string, any>
    const template = (config.template || {}) as Record<string, any>
    setChannelName(channel.name || "Telegram Bot")
    setTelegramBotToken(config.bot_token || "")
    setTelegramChatId(config.chat_id || "")
    setTelegramParseMode(template.parse_mode || "Markdown")
    setTelegramDisableNotification(Boolean(template.disable_notification))
    setTelegramMessageBody(template.body || "Praxis test message")
  }

  useEffect(() => {
    let cancelled = false
    channelsApi
      .list()
      .then((rows) => {
        if (cancelled) return
        const items = Array.isArray(rows) ? rows : []
        setSavedCount(items.length)
        if (isDingTalk) {
          const existing = items.find((item) => String(item?.provider || "") === "dingtalk") || null
          setExistingChannel(existing)
          if (existing) hydrateDingTalkDraft(existing)
        } else if (isSlack) {
          const existing = items.find((item) => String(item?.provider || "") === "slack") || null
          setExistingChannel(existing)
          if (existing) hydrateSlackDraft(existing)
        } else if (isTelegram) {
          const existing = items.find((item) => String(item?.provider || "") === "telegram") || null
          setExistingChannel(existing)
          if (existing) hydrateTelegramDraft(existing)
        } else {
          setExistingChannel(null)
        }
      })
      .catch((error) => {
        console.error("Failed to load channels:", error)
        if (!cancelled) {
          setSavedCount(0)
          setExistingChannel(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (isDingTalk) setStep(1)
    if (isSlack) setSlackStep(1)
    if (isTelegram) setTelegramStep(1)
  }, [isDingTalk, isSlack, isTelegram])

  // ── DingTalk derived values ──
  const accessToken = useMemo(() => extractAccessToken(webhookUrl), [webhookUrl])

  const ipList = useMemo(
    () =>
      ipWhitelist
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean),
    [ipWhitelist]
  )

  const atUserIdList = useMemo(
    () =>
      atUserIds
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    [atUserIds]
  )

  const requestPreview = useMemo(() => {
    const atPayload = { atUserIds: atUserIdList, isAtAll: atAll }
    if (messageType === "text") return JSON.stringify({ msgtype: "text", text: { content: messageBody }, at: atPayload }, null, 2)
    if (messageType === "markdown") return JSON.stringify({ msgtype: "markdown", markdown: { title: messageTitle, text: messageBody }, at: atPayload }, null, 2)
    if (messageType === "actionCard")
      return JSON.stringify({ msgtype: "actionCard", actionCard: { title: messageTitle, text: messageBody, btnOrientation: "0", singleTitle: t("channel.viewDetails"), singleURL: "https://www.dingtalk.com/" } }, null, 2)
    return JSON.stringify({ msgtype: "feedCard", feedCard: { links: [{ title: messageTitle, messageURL: "https://www.dingtalk.com/", picURL: "https://www.dingtalk.com/favicon.ico" }] } }, null, 2)
  }, [atAll, atUserIdList, messageBody, messageTitle, messageType, t])

  const dingtalkChecks = useMemo(
    () => [
      { label: t("channel.check.accessToken"), pass: Boolean(accessToken) },
      { label: t("channel.check.security"), pass: securityMode === "keyword" ? Boolean(keyword.trim()) : securityMode === "sign" ? Boolean(secret.trim()) : ipList.length > 0 },
      { label: t("channel.check.template"), pass: Boolean(messageBody.trim()) },
    ],
    [accessToken, ipList.length, keyword, messageBody, secret, securityMode, t]
  )

  // ── Slack derived values ──
  const slackWebhookValid = useMemo(() => {
    try {
      const parsed = new URL(slackWebhookUrl.trim())
      return parsed.protocol === "https:"
    } catch {
      return false
    }
  }, [slackWebhookUrl])

  const slackRequestPreview = useMemo(() => {
    const payload: Record<string, any> = { text: slackMessageBody }
    if (slackUsername.trim()) payload.username = slackUsername.trim()
    if (slackIconEmoji.trim()) payload.icon_emoji = slackIconEmoji.trim()
    if (slackChannel.trim()) payload.channel = slackChannel.trim()
    return JSON.stringify(payload, null, 2)
  }, [slackMessageBody, slackUsername, slackIconEmoji, slackChannel])

  const slackChecks = useMemo(
    () => [
      { label: t("channel.slack.check.webhook"), pass: slackWebhookValid },
      { label: t("channel.slack.check.template"), pass: Boolean(slackMessageBody.trim()) },
    ],
    [slackWebhookValid, slackMessageBody, t]
  )

  // ── Telegram derived values ──
  const telegramRequestPreview = useMemo(() => {
    const payload: Record<string, any> = { chat_id: telegramChatId, text: telegramMessageBody }
    if (telegramParseMode) payload.parse_mode = telegramParseMode
    if (telegramDisableNotification) payload.disable_notification = true
    return JSON.stringify(payload, null, 2)
  }, [telegramChatId, telegramMessageBody, telegramParseMode, telegramDisableNotification])

  const telegramChecks = useMemo(
    () => [
      { label: t("channel.telegram.check.botToken"), pass: Boolean(telegramBotToken.trim()) },
      { label: t("channel.telegram.check.chatId"), pass: Boolean(telegramChatId.trim()) },
      { label: t("channel.telegram.check.template"), pass: Boolean(telegramMessageBody.trim()) },
    ],
    [telegramBotToken, telegramChatId, telegramMessageBody, t]
  )

  // ── DingTalk wizard navigation ──
  const validateDingTalkStep = (targetStep: WizardStep): string | null => {
    if (targetStep === 1) {
      if (!channelName.trim()) return t("channel.validate.nameRequired")
      if (!webhookUrl.trim()) return t("channel.validate.webhookRequired")
      if (!accessToken) return t("channel.validate.tokenMissing")
      return null
    }
    if (targetStep === 2) {
      if (securityMode === "keyword" && !keyword.trim()) return t("channel.validate.keywordRequired")
      if (securityMode === "sign" && !secret.trim()) return t("channel.validate.signRequired")
      if (securityMode === "ip" && ipList.length === 0) return t("channel.validate.ipRequired")
      return null
    }
    if (!messageBody.trim()) return t("channel.validate.bodyRequired")
    if (messageType !== "text" && !messageTitle.trim()) return t("channel.validate.titleRequired")
    return null
  }

  const gotoDingTalkStep = (next: WizardStep) => {
    const currentValidation = validateDingTalkStep(step)
    if (next > step && currentValidation) {
      toast.error(currentValidation)
      return
    }
    setStep(next)
  }

  // ── save / test ──
  const saveDingTalkIntegration = async () => {
    const currentValidation = validateDingTalkStep(3)
    if (currentValidation) {
      toast.error(currentValidation)
      return
    }
    const payload: ChannelInput = {
      name: channelName.trim(),
      provider: "dingtalk",
      status: "active",
      config: {
        webhook_url: webhookUrl.trim(),
        security: { mode: securityMode, keyword: keyword.trim() || null, secret: secret.trim() || null, ip_whitelist: ipList },
        template: { type: messageType, title: messageTitle.trim(), body: messageBody.trim(), at_all: atAll, at_user_ids: atUserIdList },
      },
    }
    setSaving(true)
    try {
      const saved = existingChannel?.id ? await channelsApi.update(existingChannel.id, payload) : await channelsApi.create(payload)
      setExistingChannel(saved)
      setSavedCount((prev) => (existingChannel?.id ? prev : prev + 1))
      toast.success(t("channel.toast.saved"))
      navigate("/channel")
    } catch (error) {
      console.error("Failed to save channel config:", error)
      toast.error(t("channel.toast.saveFailed"))
    } finally {
      setSaving(false)
    }
  }

  const saveSlackIntegration = async () => {
    if (!channelName.trim()) { toast.error(t("channel.validate.nameRequired")); return }
    if (!slackWebhookUrl.trim()) { toast.error(t("channel.slack.validate.webhookRequired")); return }
    if (!slackWebhookValid) { toast.error(t("channel.slack.validate.webhookInvalid")); return }
    if (!slackMessageBody.trim()) { toast.error(t("channel.slack.validate.bodyRequired")); return }
    const payload: ChannelInput = {
      name: channelName.trim(),
      provider: "slack",
      status: "active",
      config: {
        webhook_url: slackWebhookUrl.trim(),
        template: {
          username: slackUsername.trim() || null,
          icon_emoji: slackIconEmoji.trim() || null,
          channel: slackChannel.trim() || null,
        },
      },
    }
    setSaving(true)
    try {
      const saved = existingChannel?.id ? await channelsApi.update(existingChannel.id, payload) : await channelsApi.create(payload)
      setExistingChannel(saved)
      setSavedCount((prev) => (existingChannel?.id ? prev : prev + 1))
      toast.success(t("channel.slack.toast.saved"))
      navigate("/channel")
    } catch (error) {
      console.error("Failed to save channel config:", error)
      toast.error(t("channel.toast.saveFailed"))
    } finally {
      setSaving(false)
    }
  }

  const saveTelegramIntegration = async () => {
    if (!channelName.trim()) { toast.error(t("channel.validate.nameRequired")); return }
    if (!telegramBotToken.trim()) { toast.error(t("channel.telegram.validate.botTokenRequired")); return }
    if (!telegramChatId.trim()) { toast.error(t("channel.telegram.validate.chatIdRequired")); return }
    if (!telegramMessageBody.trim()) { toast.error(t("channel.telegram.validate.bodyRequired")); return }
    const payload: ChannelInput = {
      name: channelName.trim(),
      provider: "telegram",
      status: "active",
      config: {
        bot_token: telegramBotToken.trim(),
        chat_id: telegramChatId.trim(),
        template: {
          parse_mode: telegramParseMode,
          disable_notification: telegramDisableNotification,
        },
      },
    }
    setSaving(true)
    try {
      const saved = existingChannel?.id ? await channelsApi.update(existingChannel.id, payload) : await channelsApi.create(payload)
      setExistingChannel(saved)
      setSavedCount((prev) => (existingChannel?.id ? prev : prev + 1))
      toast.success(t("channel.telegram.toast.saved"))
      navigate("/channel")
    } catch (error) {
      console.error("Failed to save channel config:", error)
      toast.error(t("channel.toast.saveFailed"))
    } finally {
      setSaving(false)
    }
  }

  const sendTestMessage = async () => {
    if (!existingChannel?.id) {
      toast.error(t("channel.toast.saveFirst"))
      return
    }
    try {
      if (isDingTalk) {
        await channelsApi.sendTest(existingChannel.id, { message_type: messageType, title: messageTitle.trim(), content: messageBody.trim() })
      } else if (isSlack) {
        await channelsApi.sendTest(existingChannel.id, { content: slackMessageBody.trim() })
      } else if (isTelegram) {
        await channelsApi.sendTest(existingChannel.id, { content: telegramMessageBody.trim() })
      }
      toast.success(t("channel.toast.testSent"))
    } catch (error) {
      console.error("Failed to send test message:", error)
      toast.error(t("channel.toast.testFailed"))
    }
  }

  // ── Card listing view ──
  if (!provider) {
    return (
      <div className="space-y-6 p-6">
        <p className="text-sm text-muted-foreground">{t("channel.listSubtitle")}({t("channel.listSubtitleCount").replace("{count}", String(savedCount))})</p>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {CHANNEL_CARDS.map((card) => (
            <Card key={card.provider}>
              <CardHeader className="space-y-1">
                <CardTitle className="text-base">{card.title}</CardTitle>
                <p className="text-sm text-muted-foreground">{card.description}</p>
              </CardHeader>
              <CardContent>
                {card.available ? (
                  <Button className="w-full" onClick={() => navigate(`/channel/${card.provider}`)} aria-label={`${t("channel.configBtn")} ${card.title}`}>
                    {t("channel.configBtn")} {card.title}
                  </Button>
                ) : (
                  <Button variant="outline" className="w-full" disabled aria-label={`${t("channel.comingSoon")} ${card.title}`}>
                    {t("channel.comingSoon")}
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    )
  }

  // ── Unsupported provider ──
  if (!isSupported) {
    return (
      <div className="space-y-4 p-6">
        <Button variant="ghost" className="w-fit px-0" onClick={() => navigate("/channel")}>
          {t("channel.backToChannel")}
        </Button>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("channel.unsupported")}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">{t("channel.unsupportedDesc")}</CardContent>
        </Card>
      </div>
    )
  }

  // ── Sidebar (checks + preview + docs) ──
  const renderSidebar = (checks: Array<{ label: string; pass: boolean }>, preview: string, referenceLinks: Array<{ title: string; url: string; updatedAt: string }>, rateLimitText: string, showSignHint?: boolean) => (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("channel.section.checks")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {checks.map((item) => (
            <div key={item.label} className="flex items-center gap-2 text-sm text-foreground">
              {item.pass ? <CheckCircle2 className="size-4 text-positive" /> : <Circle className="size-4 text-muted-foreground" />}
              <span>{item.label}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <details className="rounded-md border border-border bg-card px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium text-foreground">{t("channel.section.requestPreview")}</summary>
        <pre className="mt-2 overflow-auto rounded-md bg-muted p-2 text-xs text-foreground">{preview}</pre>
      </details>

      {showSignHint ? (
        <details className="rounded-md border border-border bg-card px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium text-foreground">{t("channel.section.signHint")}</summary>
          <pre className="mt-2 overflow-auto rounded-md bg-muted p-2 text-xs text-foreground">{`timestamp = str(round(time.time() * 1000))
string_to_sign = f"{timestamp}\\n{secret}"
sign = urlencode(base64(hmac_sha256(secret, string_to_sign)))
url = f"...&timestamp={timestamp}&sign={sign}"`}</pre>
        </details>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("channel.section.officialDocs")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {referenceLinks.map((item) => (
            <a key={item.url} href={item.url} target="_blank" rel="noreferrer" className="block rounded-md border border-border px-3 py-2 text-sm text-foreground hover:bg-muted">
              <div>{item.title}</div>
              <div className="text-xs text-muted-foreground">{t("channel.docUpdatedAt")}{item.updatedAt}</div>
            </a>
          ))}
          <p className="text-xs text-muted-foreground">{rateLimitText}</p>
        </CardContent>
      </Card>
    </div>
  )

  // ── Save/Test button bar ──
  const renderSaveBar = (onSave: () => void, showPrev?: boolean, onPrev?: () => void) => (
    <div className="flex items-center justify-between border-t border-border pt-3">
      {showPrev && onPrev ? (
        <Button variant="outline" onClick={onPrev}>{t("channel.btn.prev")}</Button>
      ) : (
        <div />
      )}
      <div className="flex items-center gap-2">
        <Button variant="outline" onClick={sendTestMessage} disabled={!existingChannel?.id || saving}>
          {t("channel.btn.sendTest")}
        </Button>
        <Button onClick={onSave} disabled={saving}>
          {saving ? <><Loader2 className="size-4 animate-spin" /> {t("channel.btn.saving")}</> : t("channel.btn.save")}
        </Button>
      </div>
    </div>
  )

  // ── DingTalk wizard ──
  if (isDingTalk) {
    return (
      <div className="space-y-6 p-6">
        <div className="space-y-3">
          <Button variant="ghost" className="w-fit px-0" onClick={() => navigate("/channel")}>{t("channel.backToChannel")}</Button>
          <p className="text-sm text-muted-foreground">{t("channel.wizardSubtitle")}</p>
          <Tabs value={String(step)} onValueChange={(value) => gotoDingTalkStep(Number(value) as WizardStep)} className="w-fit">
            <TabsList>
              {DINGTALK_STEPS.map((item) => (
                <TabsTrigger key={item.id} value={String(item.id)}>{item.id}. {item.label}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{DINGTALK_STEPS.find((item) => item.id === step)?.label}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {step === 1 ? (
                <>
                  <div className="space-y-1.5">
                    <label htmlFor="channel-name" className="text-sm font-medium text-foreground">{t("channel.label.channelName")}</label>
                    <Input id="channel-name" value={channelName} onChange={(event) => setChannelName(event.target.value)} placeholder={t("channel.placeholder.channelName")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="dingtalk-webhook" className="text-sm font-medium text-foreground">{t("channel.label.webhook")}</label>
                    <Input id="dingtalk-webhook" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." />
                  </div>
                  <div className="rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
                    {t("channel.accessTokenDetected")}{maskToken(accessToken)}
                  </div>
                </>
              ) : null}

              {step === 2 ? (
                <>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-foreground">{t("channel.label.securityStrategy")}</label>
                    <Tabs value={securityMode} onValueChange={(value) => setSecurityMode(value as SecurityMode)} className="w-fit">
                      <TabsList>
                        <TabsTrigger value="sign">{t("channel.securityMode.sign")}</TabsTrigger>
                        <TabsTrigger value="keyword">{t("channel.securityMode.keyword")}</TabsTrigger>
                        <TabsTrigger value="ip">{t("channel.securityMode.ip")}</TabsTrigger>
                      </TabsList>
                    </Tabs>
                  </div>
                  {securityMode === "keyword" ? (
                    <div className="space-y-1.5">
                      <label htmlFor="keyword" className="text-sm font-medium text-foreground">{t("channel.label.keyword")}</label>
                      <Input id="keyword" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder={t("channel.placeholder.keyword")} />
                    </div>
                  ) : null}
                  {securityMode === "sign" ? (
                    <div className="space-y-1.5">
                      <label htmlFor="secret" className="text-sm font-medium text-foreground">{t("channel.label.signSecret")}</label>
                      <Input id="secret" value={secret} onChange={(event) => setSecret(event.target.value)} placeholder="SECxxxxxxxxxxxx" />
                    </div>
                  ) : null}
                  {securityMode === "ip" ? (
                    <div className="space-y-1.5">
                      <label htmlFor="ip-whitelist" className="text-sm font-medium text-foreground">{t("channel.label.ipList")}</label>
                      <Textarea id="ip-whitelist" value={ipWhitelist} onChange={(event) => setIpWhitelist(event.target.value)} rows={4} placeholder={"1.1.1.1\n1.1.1.0/24"} />
                    </div>
                  ) : null}
                </>
              ) : null}

              {step === 3 ? (
                <>
                  <div className="space-y-1.5">
                    <label htmlFor="msg-type" className="text-sm font-medium text-foreground">{t("channel.label.messageType")}</label>
                    <NativeSelect id="msg-type" value={messageType} onChange={(event) => setMessageType(event.target.value as ChannelMessageType)} className="h-9 w-full">
                      <option value="text">text</option>
                      <option value="markdown">markdown</option>
                      <option value="actionCard">actionCard</option>
                      <option value="feedCard">feedCard</option>
                    </NativeSelect>
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="msg-title" className="text-sm font-medium text-foreground">{t("channel.label.title")}</label>
                    <Input id="msg-title" value={messageTitle} onChange={(event) => setMessageTitle(event.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="msg-body" className="text-sm font-medium text-foreground">{t("channel.label.content")}</label>
                    <Textarea id="msg-body" rows={6} value={messageBody} onChange={(event) => setMessageBody(event.target.value)} />
                  </div>
                  <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                    <label htmlFor="at-all" className="text-sm font-medium text-foreground">{t("channel.label.atAll")}</label>
                    <Switch id="at-all" checked={atAll} onCheckedChange={setAtAll} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="at-user-ids" className="text-sm font-medium text-foreground">{t("channel.label.atUserIds")}</label>
                    <Input id="at-user-ids" value={atUserIds} onChange={(event) => setAtUserIds(event.target.value)} placeholder={t("channel.placeholder.atUserIds")} />
                  </div>
                </>
              ) : null}

              <div className="flex items-center justify-between border-t border-border pt-3">
                <Button variant="outline" onClick={() => gotoDingTalkStep((Math.max(1, step - 1) as WizardStep))} disabled={step === 1}>{t("channel.btn.prev")}</Button>
                {step < 3 ? (
                  <Button onClick={() => gotoDingTalkStep((Math.min(3, step + 1) as WizardStep))}>{t("channel.btn.next")}</Button>
                ) : (
                  <div className="flex items-center gap-2">
                    <Button variant="outline" onClick={sendTestMessage} disabled={!existingChannel?.id || saving}>{t("channel.btn.sendTest")}</Button>
                    <Button onClick={saveDingTalkIntegration} disabled={saving}>
                      {saving ? <><Loader2 className="size-4 animate-spin" /> {t("channel.btn.saving")}</> : t("channel.btn.save")}
                    </Button>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {renderSidebar(dingtalkChecks, requestPreview, DINGTALK_REFERENCE_LINKS, t("channel.docRateLimit"), true)}
        </div>
      </div>
    )
  }

  // ── Slack wizard ──
  if (isSlack) {
    const SLACK_STEPS = [
      { id: 1 as const, label: t("channel.slack.step.basicInfo") },
      { id: 2 as const, label: t("channel.slack.step.messageTemplate") },
    ]

    return (
      <div className="space-y-6 p-6">
        <div className="space-y-3">
          <Button variant="ghost" className="w-fit px-0" onClick={() => navigate("/channel")}>{t("channel.backToChannel")}</Button>
          <p className="text-sm text-muted-foreground">{t("channel.slack.wizardSubtitle")}</p>
          <Tabs value={String(slackStep)} onValueChange={(value) => {
            const next = Number(value) as 1 | 2
            if (next > slackStep) {
              if (!channelName.trim()) { toast.error(t("channel.validate.nameRequired")); return }
              if (!slackWebhookUrl.trim()) { toast.error(t("channel.slack.validate.webhookRequired")); return }
              if (!slackWebhookValid) { toast.error(t("channel.slack.validate.webhookInvalid")); return }
            }
            setSlackStep(next)
          }} className="w-fit">
            <TabsList>
              {SLACK_STEPS.map((item) => (
                <TabsTrigger key={item.id} value={String(item.id)}>{item.id}. {item.label}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{SLACK_STEPS.find((item) => item.id === slackStep)?.label}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {slackStep === 1 ? (
                <>
                  <div className="space-y-1.5">
                    <label htmlFor="channel-name" className="text-sm font-medium text-foreground">{t("channel.label.channelName")}</label>
                    <Input id="channel-name" value={channelName} onChange={(event) => setChannelName(event.target.value)} placeholder={t("channel.placeholder.channelName")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="slack-webhook" className="text-sm font-medium text-foreground">{t("channel.slack.label.webhook")}</label>
                    <Input id="slack-webhook" value={slackWebhookUrl} onChange={(event) => setSlackWebhookUrl(event.target.value)} placeholder={t("channel.slack.placeholder.webhook")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="slack-username" className="text-sm font-medium text-foreground">{t("channel.slack.label.username")}</label>
                    <Input id="slack-username" value={slackUsername} onChange={(event) => setSlackUsername(event.target.value)} placeholder={t("channel.slack.placeholder.username")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="slack-icon-emoji" className="text-sm font-medium text-foreground">{t("channel.slack.label.iconEmoji")}</label>
                    <Input id="slack-icon-emoji" value={slackIconEmoji} onChange={(event) => setSlackIconEmoji(event.target.value)} placeholder={t("channel.slack.placeholder.iconEmoji")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="slack-channel" className="text-sm font-medium text-foreground">{t("channel.slack.label.channel")}</label>
                    <Input id="slack-channel" value={slackChannel} onChange={(event) => setSlackChannel(event.target.value)} placeholder={t("channel.slack.placeholder.channel")} />
                  </div>
                </>
              ) : null}

              {slackStep === 2 ? (
                <div className="space-y-1.5">
                  <label htmlFor="slack-msg-body" className="text-sm font-medium text-foreground">{t("channel.slack.label.messageBody")}</label>
                  <Textarea id="slack-msg-body" rows={6} value={slackMessageBody} onChange={(event) => setSlackMessageBody(event.target.value)} />
                </div>
              ) : null}

              {slackStep === 1 ? (
                <div className="flex items-center justify-end border-t border-border pt-3">
                  <Button onClick={() => {
                    if (!channelName.trim()) { toast.error(t("channel.validate.nameRequired")); return }
                    if (!slackWebhookUrl.trim()) { toast.error(t("channel.slack.validate.webhookRequired")); return }
                    if (!slackWebhookValid) { toast.error(t("channel.slack.validate.webhookInvalid")); return }
                    setSlackStep(2)
                  }}>{t("channel.btn.next")}</Button>
                </div>
              ) : (
                renderSaveBar(saveSlackIntegration, true, () => setSlackStep(1))
              )}
            </CardContent>
          </Card>

          {renderSidebar(slackChecks, slackRequestPreview, SLACK_REFERENCE_LINKS, t("channel.slack.rateLimit"))}
        </div>
      </div>
    )
  }

  // ── Telegram wizard ──
  if (isTelegram) {
    const TELEGRAM_STEPS = [
      { id: 1 as const, label: t("channel.telegram.step.basicInfo") },
      { id: 2 as const, label: t("channel.telegram.step.messageTemplate") },
    ]

    return (
      <div className="space-y-6 p-6">
        <div className="space-y-3">
          <Button variant="ghost" className="w-fit px-0" onClick={() => navigate("/channel")}>{t("channel.backToChannel")}</Button>
          <p className="text-sm text-muted-foreground">{t("channel.telegram.wizardSubtitle")}</p>
          <Tabs value={String(telegramStep)} onValueChange={(value) => {
            const next = Number(value) as 1 | 2
            if (next > telegramStep) {
              if (!channelName.trim()) { toast.error(t("channel.validate.nameRequired")); return }
              if (!telegramBotToken.trim()) { toast.error(t("channel.telegram.validate.botTokenRequired")); return }
              if (!telegramChatId.trim()) { toast.error(t("channel.telegram.validate.chatIdRequired")); return }
            }
            setTelegramStep(next)
          }} className="w-fit">
            <TabsList>
              {TELEGRAM_STEPS.map((item) => (
                <TabsTrigger key={item.id} value={String(item.id)}>{item.id}. {item.label}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{TELEGRAM_STEPS.find((item) => item.id === telegramStep)?.label}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {telegramStep === 1 ? (
                <>
                  <div className="space-y-1.5">
                    <label htmlFor="channel-name" className="text-sm font-medium text-foreground">{t("channel.label.channelName")}</label>
                    <Input id="channel-name" value={channelName} onChange={(event) => setChannelName(event.target.value)} placeholder={t("channel.placeholder.channelName")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="telegram-bot-token" className="text-sm font-medium text-foreground">{t("channel.telegram.label.botToken")}</label>
                    <Input id="telegram-bot-token" value={telegramBotToken} onChange={(event) => setTelegramBotToken(event.target.value)} placeholder={t("channel.telegram.placeholder.botToken")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="telegram-chat-id" className="text-sm font-medium text-foreground">{t("channel.telegram.label.chatId")}</label>
                    <Input id="telegram-chat-id" value={telegramChatId} onChange={(event) => setTelegramChatId(event.target.value)} placeholder={t("channel.telegram.placeholder.chatId")} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="telegram-parse-mode" className="text-sm font-medium text-foreground">{t("channel.telegram.label.parseMode")}</label>
                    <NativeSelect id="telegram-parse-mode" value={telegramParseMode} onChange={(event) => setTelegramParseMode(event.target.value as "Markdown" | "HTML")} className="h-9 w-full">
                      <option value="Markdown">Markdown</option>
                      <option value="HTML">HTML</option>
                    </NativeSelect>
                  </div>
                  <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                    <label htmlFor="telegram-disable-notification" className="text-sm font-medium text-foreground">{t("channel.telegram.label.disableNotification")}</label>
                    <Switch id="telegram-disable-notification" checked={telegramDisableNotification} onCheckedChange={setTelegramDisableNotification} />
                  </div>
                </>
              ) : null}

              {telegramStep === 2 ? (
                <div className="space-y-1.5">
                  <label htmlFor="telegram-msg-body" className="text-sm font-medium text-foreground">{t("channel.telegram.label.messageBody")}</label>
                  <Textarea id="telegram-msg-body" rows={6} value={telegramMessageBody} onChange={(event) => setTelegramMessageBody(event.target.value)} />
                </div>
              ) : null}

              {telegramStep === 1 ? (
                <div className="flex items-center justify-end border-t border-border pt-3">
                  <Button onClick={() => {
                    if (!channelName.trim()) { toast.error(t("channel.validate.nameRequired")); return }
                    if (!telegramBotToken.trim()) { toast.error(t("channel.telegram.validate.botTokenRequired")); return }
                    if (!telegramChatId.trim()) { toast.error(t("channel.telegram.validate.chatIdRequired")); return }
                    setTelegramStep(2)
                  }}>{t("channel.btn.next")}</Button>
                </div>
              ) : (
                renderSaveBar(saveTelegramIntegration, true, () => setTelegramStep(1))
              )}
            </CardContent>
          </Card>

          {renderSidebar(telegramChecks, telegramRequestPreview, TELEGRAM_REFERENCE_LINKS, t("channel.telegram.rateLimit"))}
        </div>
      </div>
    )
  }

  return null
}
