import { memo, useEffect, useId, useLayoutEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ArchiveRestore, ArrowDown, ArrowUp, Check, ChevronRight, CircleAlert, Gauge, Loader2, Wrench } from "lucide-react"
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { useShellI18n } from "@/i18n/shellI18nContext"
import { terminalRun, type ContextCompressionNotice, type Reconciliation, type RunContextStatus, type RunEvent, type RunScene, type RunView, type ToolBlock } from "@/lib/agentRuns"
import { useRunConversation } from "./useRunConversation"

const Markdown = memo(function Markdown({ text }: { text: string }) {
  return <div className="max-w-none break-words text-sm leading-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_table]:block [&_table]:overflow-x-auto">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: props => <a {...props} target="_blank" rel="noopener noreferrer" className="underline" /> }}>{text}</ReactMarkdown>
  </div>
})

function ReconcileForm({ call, onSave }: { call: ToolBlock; onSave: (check: Reconciliation) => void }) {
  const { t } = useShellI18n()
  const id = useId()
  const [resolution, setResolution] = useState<Reconciliation["resolution"] | "">("")
  const [evidence, setEvidence] = useState("")
  const [stopped, setStopped] = useState(false)
  return <form className="space-y-3" onSubmit={event => { event.preventDefault(); if (resolution && stopped && evidence.trim()) onSave({ resolution, evidence: evidence.trim(), execution_stopped: true }) }}>
    <p className="text-xs text-muted-foreground">{t("runtime.reconcileHelp")}</p>
    <label className="block" htmlFor={`${id}-result`}>{t("runtime.checkedOutcome")}</label>
    <select id={`${id}-result`} required className="min-h-11 w-full rounded-md border border-input bg-background px-3 focus-visible:outline-2 focus-visible:outline-ring" value={resolution} onChange={event => setResolution(event.target.value as typeof resolution)} disabled={call.submitting}>
      <option value="">{t("runtime.chooseCheckedOutcome")}</option>
      <option value="succeeded">{t("runtime.confirmedSucceeded")}</option>
      <option value="failed">{t("runtime.confirmedFailed")}</option>
      <option value="not_executed">{t("runtime.confirmedNotExecuted")}</option>
    </select>
    <label className="block" htmlFor={`${id}-evidence`}>{t("runtime.checkEvidence")}</label>
    <textarea id={`${id}-evidence`} required maxLength={10000} className="min-h-24 w-full rounded-md border border-input bg-background p-3 focus-visible:outline-2 focus-visible:outline-ring" value={evidence} onChange={event => setEvidence(event.target.value)} disabled={call.submitting} />
    <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={stopped} disabled={call.submitting} onChange={event => setStopped(event.target.checked)} />{t("runtime.externalExecutionEnded")}</label>
    <Button variant="outline" type="submit" className="min-h-11" disabled={!resolution || !stopped || !evidence.trim() || call.submitting}>{call.submitting ? t("runtime.recordingCheck") : t("runtime.recordCheck")}</Button>
  </form>
}

function ToolCard({ call, approve, reconcile, pendingCount }: { call: ToolBlock; approve: (value: boolean) => void; reconcile?: (check: Reconciliation) => void; pendingCount: number }) {
  const { t } = useShellI18n()
  const important = ["waiting_approval", "failed", "denied", "outcome_unknown", "interrupted"].includes(call.status)
  const [disclosure, setDisclosure] = useState({ status: call.status, open: important })
  const expanded = disclosure.status === call.status ? disclosure.open : important
  const pending = call.status === "waiting_approval" && call.decision === "pending"
  const labels: Record<string, string> = { executing: t("runtime.executing"), succeeded: t("runtime.executed"), failed: t("runtime.failed"), denied: t("runtime.denied"), outcome_unknown: t("runtime.outcomeUnknownReconcile"), interrupted: t("runtime.interrupted"), waiting_approval: t("runtime.awaitingApproval"), pending: t("runtime.pending") }
  return <div className={`my-3 min-w-0 overflow-hidden rounded-lg border ${important ? "border-warning/40 bg-warning/5" : "border-border bg-muted/30"}`} data-tool-id={call.id}>
    <button type="button" className="flex min-h-10 w-full items-center gap-2 px-3 text-left text-sm transition-colors hover:bg-muted/70 focus-visible:outline-2 focus-visible:outline-ring" aria-expanded={expanded} onClick={() => setDisclosure({ status: call.status, open: !expanded })}>
      {call.status === "succeeded" ? <Check className="size-4 shrink-0" /> : important ? <CircleAlert className="size-4 shrink-0" /> : <Wrench className="size-4 shrink-0" />}
      <span className="min-w-0 break-all font-medium">{call.name}</span>
      <span className="ml-auto shrink-0 text-xs text-muted-foreground">{call.reconciled ? t("runtime.userReportedCheck") : labels[call.status] ?? call.status}</span>
      <ChevronRight className={`size-4 shrink-0 ${expanded ? "rotate-90" : ""}`} />
    </button>
    {expanded && <div className="space-y-3 border-t border-border px-3 py-3 text-sm">
      <p className="text-xs font-medium text-muted-foreground">{t("runtime.targetAndArguments")}</p>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-background p-3 font-mono text-xs">{JSON.stringify({ target: call.target, arguments: call.arguments }, null, 2)}</pre>
      {call.result !== undefined && <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md border border-dashed border-border bg-background p-3 font-mono text-xs">{typeof call.result === "string" ? call.result : JSON.stringify(call.result, null, 2)}</pre>}
      {call.status === "outcome_unknown" && <p role="alert">{t("runtime.theOperationMayHaveTakenEffectStopping")}</p>}
      {call.status === "outcome_unknown" && call.fingerprint && reconcile && <ReconcileForm call={call} onSave={reconcile} />}
      {call.reconciled && <p className="text-xs text-muted-foreground">{t("runtime.checkNotPlatformVerified")}</p>}
      {call.autoApproved && <p role="status" className="text-xs text-muted-foreground">{t("runtime.autoApprovalAudit")}</p>}
      {pending && <div className="flex gap-2">
        <Button className="min-h-11" disabled={call.submitting} onClick={() => approve(true)}>{t("runtime.approveAction")}</Button>
        <Button variant="outline" className="min-h-11" disabled={call.submitting} onClick={() => approve(false)}>{t("runtime.denyAction")}</Button>
      </div>}
      {call.status === "waiting_approval" && call.decision && call.decision !== "pending" && <p role="status" className="text-muted-foreground">{t("runtime.decisionRecordedWaitingForThisBatchExecution")}</p>}
      {call.status === "waiting_approval" && pendingCount > 0 && <p className="text-xs text-muted-foreground">{t("runtime.approvalsRemaining").replace("{count}", String(pendingCount))}</p>}
      {call.error && <p role="alert" className="text-destructive">{t(call.error === "reconciliation" ? "runtime.checkNotRecorded" : "runtime.theDecisionCouldNotBeConfirmedCheck")}</p>}
    </div>}
  </div>
}

function ContextUsageIndicator({ status }: { status: RunContextStatus }) {
  const { t } = useShellI18n()
  const percent = Math.max(0, Math.min(100, status.used_percent))
  if (status.state === "compressing") return <div data-testid="chat-context-usage" data-state="compressing" role="status" aria-live="polite" aria-busy="true" className="flex min-h-6 items-center gap-2 px-1 text-xs text-warning"><Loader2 className="size-3.5 animate-spin" /><span>{t("chat.context.compressing")}</span></div>
  const failed = status.state === "compression_failed"
  const warning = percent >= status.compression_threshold_percent * 0.85
  const label = t(failed ? "chat.context.compressionFailed" : "chat.context.label")
  return <TooltipProvider delayDuration={150}><Tooltip><TooltipTrigger asChild>
    <div data-testid="chat-context-usage" data-state={status.state} className="flex min-h-6 w-fit items-center gap-2 px-1 text-xs text-muted-foreground">
      <Gauge className="size-3.5 shrink-0" /><span className={failed ? "text-destructive" : ""}>{label}</span>
      <Progress value={percent} aria-label={`${label} ${percent.toFixed(1)}%`} className="w-24" indicatorClassName={failed ? "bg-destructive" : warning ? "bg-warning" : "bg-primary"} />
      <span className="min-w-10 tabular-nums text-foreground">{percent.toFixed(1)}%</span>
    </div>
  </TooltipTrigger><TooltipContent side="top" sideOffset={6}>
    <p className="tabular-nums">{Math.round(status.estimated_tokens).toLocaleString()} / {status.context_window_tokens.toLocaleString()} {t("runtime.tokenUnit")} · {t("chat.context.windowUsage")}</p>
    <p>{t("chat.context.compressAt")} {status.compression_threshold_percent}% · {t(status.token_source === "provider" ? "chat.context.measured" : "chat.context.estimated")}</p>
  </TooltipContent></Tooltip></TooltipProvider>
}

function ContextCompressionBanner({ notice }: { notice: ContextCompressionNotice }) {
  const { t } = useShellI18n()
  return <div data-testid="chat-context-compressed" className="flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-xs text-accent-foreground">
    <ArchiveRestore className="size-3.5 shrink-0 text-primary" />
    <p className="min-w-0 flex-1">{t("chat.context.compressed")}<span className="mx-2 text-muted-foreground">·</span><span className="tabular-nums">{notice.before_percent.toFixed(1)}% → {notice.after_percent.toFixed(1)}%</span></p>
  </div>
}

function RunState({ view, stop, reconnect, resume }: { view: RunView; stop: () => void; reconnect: () => void; resume: () => void }) {
  const { t } = useShellI18n()
  const [now, setNow] = useState(() => Date.now())
  const active = !terminalRun(view.run.status)
  useEffect(() => { if (!active) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [active])
  let label = ""
  if (view.connection === "unavailable") label = t("runtime.runRecordsAreUnavailableCheckAccessOr")
  else if (view.run.cancel_requested) label = t("runtime.stoppingCompletedOperationsAreNotRolledBack")
  else if (view.connection === "reconnecting") label = t("runtime.reconnectingTheTaskMayStillBeRunning")
  else if (active) {
    const labels = { queued: t("runtime.queued"), context: t("runtime.compactingContext"), model: t("runtime.waitingForModel"), text: t("runtime.receivingResponse"), tool: t("runtime.toolExecuting"), approval: t("runtime.awaitingApproval") }
    label = labels[view.activity ?? "queued"]
  } else if (view.run.status !== "finished") {
    const labels = { cancelled: t("runtime.stopped"), failed: t("runtime.runFailed"), interrupted: t("runtime.interruptedReconcileBeforeResuming"), limited: t("runtime.runLimitReached") }
    label = view.run.error_code === "context_limit" ? t("runtime.contextLimit") : labels[view.run.status as keyof typeof labels] ?? view.run.status
  }
  if (!label && !view.error) return null
  return <div className="mt-3 flex min-h-8 flex-wrap items-center gap-2 text-xs text-muted-foreground">
    <span role="status">{label}{active ? ` · ${Math.max(0, Math.floor((now - view.activityAt) / 1000))}s` : ""}</span>
    {view.run.error_code && <code>{view.run.error_code}</code>}
    {active && <Button variant="ghost" size="sm" className="min-h-11 px-2.5" onClick={stop} disabled={view.run.cancel_requested}>{t("runtime.stop")}</Button>}
    {view.error === "cancel" && <span role="alert" className="text-destructive">{t("runtime.stopRequestWasNotConfirmedRetry")}</span>}
    {view.run.status === "interrupted" && !view.run.cancel_requested && <Button variant="outline" size="sm" className="min-h-11" onClick={resume} disabled={view.resuming || !view.terminalSeen || view.cursor < view.run.event_seq || view.blocks.some(block => block.kind === "tool" && ["outcome_unknown", "executing"].includes(block.status))}>{view.resuming ? t("runtime.resuming") : t("runtime.resumeRun")}</Button>}
    {view.error === "resume" && <span role="alert" className="text-destructive">{t("runtime.resumeNotConfirmed")}</span>}
    {(view.connection === "unavailable" || view.error === "resume") && <Button variant="outline" size="sm" className="min-h-11" onClick={reconnect}>{t("runtime.reconnect")}</Button>}
  </div>
}

type Props = { conversationId: string | null; scene?: RunScene; title?: string; disabled?: boolean; onEvent?: (event: RunEvent, view: RunView) => void }
export function RunConversationView(props: Props) {
  return <RunConversationSession key={props.conversationId ?? "new"} {...props} />
}

function RunConversationSession({ conversationId, scene = {}, title = "Praxis", disabled = false, onEvent }: Props) {
  const { locale, t } = useShellI18n()
  const controller = useRunConversation(conversationId, onEvent)
  const [input, setInput] = useState("")
  const inputId = useId()
  const [atBottom, setAtBottom] = useState(true)
  const viewport = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const active = controller.views.some(view => !view.delivery && !terminalRun(view.run.status))
  useLayoutEffect(() => { if (following.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight }, [controller.views])
  useLayoutEffect(() => {
    const element = viewport.current
    const body = content.current
    if (!element || !body) return
    const observer = new ResizeObserver(() => {
      // A narrower viewport or taller composer can hide the latest answer even
      // without new model events. Only follow while the reader was at the end.
      if (following.current) element.scrollTop = element.scrollHeight
    })
    observer.observe(element)
    observer.observe(body)
    return () => observer.disconnect()
  }, [])
  const send = (mode: "append" | "stop_and_modify") => {
    if (!input.trim() || !conversationId || disabled || controller.loading || controller.loadError) return
    const prompt = input; setInput("")
    void controller.send(prompt, { ...scene, locale }, mode)
  }
  const suggestions = [
    [t("chat.suggestion.slowSql.label"), t("chat.suggestion.slowSql.prompt")],
    [t("chat.suggestion.conn.label"), t("chat.suggestion.conn.prompt")],
    [t("chat.suggestion.schema.label"), t("chat.suggestion.schema.prompt")],
    [t("chat.suggestion.health.label"), t("chat.suggestion.health.prompt")],
  ]
  const contextView = [...controller.views].reverse().find(view => view.contextStatus || view.run.model)
  const contextStatus = contextView?.contextStatus ?? {
    context_window_tokens: contextView?.run.model?.context_window_tokens ?? 128000,
    estimated_tokens: 0,
    used_percent: 0,
    compression_threshold_percent: contextView?.run.model?.context_compression_threshold_percent ?? 75,
    compression_threshold_tokens: Math.round((contextView?.run.model?.context_window_tokens ?? 128000) * (contextView?.run.model?.context_compression_threshold_percent ?? 75) / 100),
    remaining_tokens: contextView?.run.model?.context_window_tokens ?? 128000,
    token_source: "estimate" as const,
    state: "ready" as const,
  }
  const compressionNotice = [...controller.views].reverse().find(view => view.contextCompressionNotice)?.contextCompressionNotice
  return <section className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm" aria-label={t("runtime.conversation")}>
    <div ref={viewport} data-testid="run-viewport" className="relative min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain" onScroll={() => {
      const element = viewport.current
      if (!element) return
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 64
      setAtBottom(following.current)
    }}>
      <div ref={content} className="flex min-h-full w-full flex-col px-4 py-6 sm:px-8">
        {controller.loading && <p role="status" className="text-sm text-muted-foreground">{t("runtime.loadingConversation")}</p>}
        {controller.loadError && <div role="alert"><p>{t("runtime.conversationCouldNotBeLoadedExistingTasks")}</p><Button variant="outline" onClick={controller.refresh}>{t("runtime.reload")}</Button></div>}
        {!controller.loading && !controller.views.length && !controller.loadError && <div className="my-auto flex grow flex-col justify-center py-8">
          <div className="pb-6">
            <h2 className="text-xl font-semibold text-foreground">{t("thread.welcome.title")}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{t("thread.welcome.subtitle")}</p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {suggestions.map(([label, prompt]) => <Button key={label} type="button" variant="ghost" className="h-auto justify-start rounded-xl border border-border bg-card px-4 py-3 text-left text-sm font-medium hover:bg-muted" onClick={() => setInput(prompt)}>{label}</Button>)}
          </div>
        </div>}
        <div className="space-y-8">
        {controller.views.map(view => <article key={view.clientRequestId ?? view.run.id} data-run-id={view.run.id} className="min-w-0 space-y-6">
          <div className="ml-auto w-fit max-w-[90%] rounded-xl bg-primary px-4 py-2.5 text-primary-foreground"><p className="whitespace-pre-wrap break-words text-sm leading-6">{view.run.prompt}</p>
            {view.delivery === "sending" && <span className="text-xs text-primary-foreground/70" role="status">{t("runtime.sending")}</span>}
            {view.delivery === "failed" && <div role="alert" className="mt-2 text-xs text-primary-foreground">{t("runtime.submissionWasNotConfirmedRetryingWillNot")}<Button size="sm" variant="secondary" onClick={() => void controller.send(view.run.prompt, view.submittedScene ?? {}, view.submittedMode, view.clientRequestId)}>{t("runtime.retrySubmission")}</Button></div>}
          </div>
          {!view.delivery && <div className="min-w-0 animate-in fade-in slide-in-from-bottom-1 duration-150"><p className="mb-2 text-xs font-medium text-muted-foreground">{title}</p>
            {view.blocks.map(block => block.kind === "text" ? <div key={block.id} data-message-id={block.messageId}><Markdown text={block.text} /></div> : <ToolCard key={block.id} call={block} pendingCount={view.blocks.filter(item => item.kind === "tool" && item.status === "waiting_approval" && item.decision === "pending").length} approve={approved => void controller.decide(view.run.id, block, approved)} reconcile={terminalRun(view.run.status) ? check => void controller.reconcile(view.run.id, block, check) : undefined} />)}
            <RunState view={view} stop={() => void controller.cancel(view.run.id)} reconnect={() => controller.reconnect(view.run.id)} resume={() => void controller.resume(view.run.id)} />
          </div>}
        </article>)}
        </div>
      </div>
    </div>
    <div className="relative w-full shrink-0 space-y-2 px-4 pb-5 sm:px-8">
      {!atBottom && <TooltipIconButton tooltip={t("runtime.jumpToLatest")} variant="outline" className="absolute -top-11 left-1/2 z-10 size-9 -translate-x-1/2 rounded-full bg-card shadow-sm" onClick={() => { following.current = true; setAtBottom(true); if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight }}><ArrowDown className="size-4" /></TooltipIconButton>}
    {compressionNotice && <ContextCompressionBanner notice={compressionNotice} />}
    <ContextUsageIndicator status={contextStatus} />
    <form onSubmit={event => { event.preventDefault(); send("append") }}>
      <label htmlFor={inputId} className="sr-only">{t("runtime.message")}</label>
      <div className="rounded-xl border border-border bg-card p-2 shadow-sm transition-shadow focus-within:border-ring/75 focus-within:ring-2 focus-within:ring-ring/20">
        <textarea id={inputId} rows={1} className="max-h-40 min-h-10 w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-muted-foreground/70" value={input} onChange={event => setInput(event.target.value)} disabled={disabled || !conversationId || controller.loading || controller.loadError} placeholder={t("thread.input.placeholder")} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send("append") }
        }} />
        <div className="flex flex-wrap items-center justify-end gap-2">
          {active && <Button type="button" variant="ghost" size="sm" className="h-8" disabled={disabled || !input.trim()} onClick={() => send("stop_and_modify")}>{t("runtime.stopAndModify")}</Button>}
          <TooltipIconButton type="submit" variant="default" tooltip={active ? t("runtime.queueMessage") : t("runtime.send")} aria-label={active ? t("runtime.queueMessage") : t("runtime.send")} className="size-8 rounded-full" disabled={disabled || !input.trim() || !conversationId || controller.loading || controller.loadError}><ArrowUp className="size-4" /></TooltipIconButton>
        </div>
      </div>
      {active && <p className="mt-2 text-xs text-muted-foreground">{t("runtime.additionalMessagesAreQueuedStoppingDoesNot")}</p>}
    </form>
    </div>
  </section>
}
