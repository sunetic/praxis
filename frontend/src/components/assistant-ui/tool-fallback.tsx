"use client";

import { memo, useCallback, useContext, useRef, useState } from "react";
import {
  AlertCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  LoaderIcon,
  XCircleIcon,
} from "lucide-react";
import {
  useScrollLock,
  type ToolCallMessagePartStatus,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";
import {
  PendingActionContext,
  getPendingActionToken,
} from "@/components/assistant-ui/pending-action-context";
import { cn } from "@/lib/utils";
import type { PendingAction } from "@/lib/api";
import { useShellI18n } from "@/i18n/shellI18n";

const ANIMATION_DURATION = 200;

export type ToolFallbackRootProps = Omit<
  React.ComponentProps<typeof Collapsible>,
  "open" | "onOpenChange"
> & {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  defaultOpen?: boolean;
};

function ToolFallbackRoot({
  className,
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  defaultOpen = false,
  children,
  ...props
}: ToolFallbackRootProps) {
  const collapsibleRef = useRef<HTMLDivElement>(null);
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const lockScroll = useScrollLock(collapsibleRef, ANIMATION_DURATION);

  const isControlled = controlledOpen !== undefined;
  const isOpen = isControlled ? controlledOpen : uncontrolledOpen;

  const handleOpenChange = useCallback(
    (open: boolean) => {
      if (!open) {
        lockScroll();
      }
      if (!isControlled) {
        setUncontrolledOpen(open);
      }
      controlledOnOpenChange?.(open);
    },
    [lockScroll, isControlled, controlledOnOpenChange],
  );

  return (
    <Collapsible
      ref={collapsibleRef}
      data-slot="tool-fallback-root"
      open={isOpen}
      onOpenChange={handleOpenChange}
      className={cn(
        "aui-tool-fallback-root group/tool-fallback-root w-full rounded-lg border py-3",
        className,
      )}
      style={
        {
          "--animation-duration": `${ANIMATION_DURATION}ms`,
        } as React.CSSProperties
      }
      {...props}
    >
      {children}
    </Collapsible>
  );
}

type ToolStatus = ToolCallMessagePartStatus["type"];

const statusIconMap: Record<ToolStatus, React.ElementType> = {
  running: LoaderIcon,
  complete: CheckIcon,
  incomplete: XCircleIcon,
  "requires-action": AlertCircleIcon,
};

function ToolFallbackTrigger({
  toolName,
  status,
  result,
  pendingAction,
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  toolName: string;
  status?: ToolCallMessagePartStatus;
  result?: unknown;
  pendingAction?: PendingAction;
}) {
  const { locale, t } = useShellI18n();
  const statusType = status?.type ?? "complete";
  const isRunning = statusType === "running";
  const isCancelled =
    status?.type === "incomplete" && status.reason === "cancelled";

  const Icon = pendingAction ? AlertCircleIcon : statusIconMap[statusType];
  const label = pendingAction
    ? t("tool.approval.required")
    : isCancelled
      ? t("tool.cancelled")
      : t("tool.used");
  const resultSummary = pendingAction ? null : getToolResultSummary(result, locale);

  return (
    <CollapsibleTrigger
      data-slot="tool-fallback-trigger"
      className={cn(
        "aui-tool-fallback-trigger group/trigger flex w-full items-center gap-2 px-4 text-sm transition-colors",
        className,
      )}
      {...props}
    >
      <Icon
        data-slot="tool-fallback-trigger-icon"
        className={cn(
          "aui-tool-fallback-trigger-icon size-4 shrink-0",
          isCancelled && "text-muted-foreground",
          pendingAction && "text-warning",
          isRunning && "animate-spin",
        )}
      />
      <span
        data-slot="tool-fallback-trigger-label"
        className={cn(
          "aui-tool-fallback-trigger-label-wrapper relative inline-block grow text-start leading-none",
          isCancelled && "text-muted-foreground line-through",
        )}
      >
        <span>{label}{pendingAction ? "" : toolName}</span>
        {isRunning && (
          <span
            aria-hidden
            data-slot="tool-fallback-trigger-shimmer"
            className="aui-tool-fallback-trigger-shimmer shimmer pointer-events-none absolute inset-0 motion-reduce:animate-none"
          >
            {label}{pendingAction ? "" : toolName}
          </span>
        )}
      </span>
      {resultSummary ? (
        <span
          className={cn(
            "max-w-[48%] truncate text-xs text-muted-foreground",
            resultSummary.isError && "text-destructive",
          )}
        >
          {resultSummary.text}
        </span>
      ) : null}
      <ChevronDownIcon
        data-slot="tool-fallback-trigger-chevron"
        className={cn(
          "aui-tool-fallback-trigger-chevron size-4 shrink-0",
          "transition-transform duration-(--animation-duration) ease-out",
          "group-data-[state=closed]/trigger:-rotate-90",
          "group-data-[state=open]/trigger:rotate-0",
        )}
      />
    </CollapsibleTrigger>
  );
}

function ToolFallbackContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      data-slot="tool-fallback-content"
      className={cn(
        "aui-tool-fallback-content relative overflow-hidden text-sm outline-none",
        "group/collapsible-content ease-out",
        "data-[state=closed]:animate-collapsible-up",
        "data-[state=open]:animate-collapsible-down",
        "data-[state=closed]:fill-mode-forwards",
        "data-[state=closed]:pointer-events-none",
        "data-[state=open]:duration-(--animation-duration)",
        "data-[state=closed]:duration-(--animation-duration)",
        className,
      )}
      {...props}
    >
      <div className="mt-3 flex flex-col gap-2 border-t pt-2">{children}</div>
    </CollapsibleContent>
  );
}

function ToolFallbackArgs({
  argsText,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  argsText?: string;
}) {
  if (!argsText) return null;

  return (
    <div
      data-slot="tool-fallback-args"
      className={cn("aui-tool-fallback-args px-4", className)}
      {...props}
    >
      <pre className="aui-tool-fallback-args-value whitespace-pre-wrap">
        {argsText}
      </pre>
    </div>
  );
}

function ToolFallbackResult({
  result,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  result?: unknown;
}) {
  const { t } = useShellI18n();
  if (result === undefined) return null;

  return (
    <div
      data-slot="tool-fallback-result"
      className={cn(
        "aui-tool-fallback-result border-t border-dashed px-4 pt-2",
        className,
      )}
      {...props}
    >
      <p className="aui-tool-fallback-result-header font-semibold">{t("tool.result")}</p>
      <pre className="aui-tool-fallback-result-content whitespace-pre-wrap">
        {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
      </pre>
    </div>
  );
}

function getToolResultSummary(
  result: unknown,
  locale: string,
): { text: string; isError: boolean } | null {
  const resultRecord = result && typeof result === "object"
    ? result as Record<string, unknown>
    : null;
  const data = resultRecord?.data && typeof resultRecord.data === "object"
    ? resultRecord.data as Record<string, unknown>
    : null;
  const rowCount = typeof data?.row_count === "number"
    ? data.row_count
    : Array.isArray(data?.rows) ? data.rows.length : null;
  if (data?.cancelled === true || data?.cancelled_action_token) {
    return {
      text: locale === "zh-CN" ? "已由用户取消" : "Cancelled by user",
      isError: false,
    };
  }
  if (resultRecord?.success === true && rowCount !== null) {
    return {
      text: locale === "zh-CN"
        ? `执行成功，返回 ${rowCount} 条记录`
        : `Succeeded, returned ${rowCount} ${rowCount === 1 ? "row" : "rows"}`,
      isError: false,
    };
  }
  const errorRecord = resultRecord?.error && typeof resultRecord.error === "object"
    ? resultRecord.error as Record<string, unknown>
    : null;
  const errorMessage = typeof errorRecord?.message === "string" ? errorRecord.message : null;
  if (resultRecord?.success === false && errorMessage) {
    return {
      text: locale === "zh-CN"
        ? `执行失败：${errorMessage}`
        : `Execution failed: ${errorMessage}`,
      isError: true,
    };
  }
  return null;
}

function PendingActionApproval({
  action,
  processing,
  disabled,
  onConfirm,
  onCancel,
}: {
  action: PendingAction;
  processing: boolean;
  disabled: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useShellI18n();
  const description = action.intent || action.source_text || t("tool.approval.description");
  const preview = action.sql_preview || action.preview;
  const target = [action.cluster_key, action.resolved_access_level || action.resolved_role]
    .filter((value): value is string => Boolean(value))
    .join(" · ");

  return (
    <div data-slot="tool-approval" className="space-y-3 px-4 pb-1">
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">{description}</p>
        {target ? (
          <p className="text-xs text-muted-foreground">
            {t("tool.approval.target")}{target}
          </p>
        ) : null}
      </div>
      {preview ? (
        <pre className="max-h-36 overflow-auto rounded-md bg-muted px-3 py-2 text-xs leading-relaxed text-foreground whitespace-pre-wrap">
          {preview}
        </pre>
      ) : null}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          className="h-8 px-3 text-xs"
          disabled={disabled}
          onClick={onConfirm}
        >
          {processing ? <LoaderIcon className="size-3.5 animate-spin" /> : t("tool.approval.confirm")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-8 px-3 text-xs"
          disabled={disabled}
          onClick={onCancel}
        >
          {t("tool.approval.cancel")}
        </Button>
        <span className="text-xs text-muted-foreground">{t("tool.approval.paused")}</span>
      </div>
    </div>
  );
}

function ToolFallbackError({
  status,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  status?: ToolCallMessagePartStatus;
}) {
  if (status?.type !== "incomplete") return null;

  const error = status.error;
  const errorText = error
    ? typeof error === "string"
      ? error
      : JSON.stringify(error)
    : null;

  if (!errorText) return null;

  const isCancelled = status.reason === "cancelled";
  const headerText = isCancelled ? "Cancelled reason:" : "Error:";

  return (
    <div
      data-slot="tool-fallback-error"
      className={cn("aui-tool-fallback-error px-4", className)}
      {...props}
    >
      <p className="aui-tool-fallback-error-header font-semibold text-muted-foreground">
        {headerText}
      </p>
      <p className="aui-tool-fallback-error-reason text-muted-foreground">
        {errorText}
      </p>
    </div>
  );
}

const ToolFallbackImpl: ToolCallMessagePartComponent = ({
  toolName,
  argsText,
  result,
  status,
}) => {
  const pendingContext = useContext(PendingActionContext);
  const actionToken = getPendingActionToken(result);
  const pendingAction = actionToken
    ? pendingContext?.actionsByToken.get(actionToken)
    : undefined;
  const processing = Boolean(actionToken && pendingContext?.processingToken === actionToken);
  const isCancelled =
    status?.type === "incomplete" && status.reason === "cancelled";
  const requiresAttention =
    Boolean(pendingAction) ||
    status?.type === "requires-action" ||
    (status?.type === "incomplete" && status.reason !== "cancelled");
  const [userOpen, setUserOpen] = useState(false);
  const open = requiresAttention || userOpen;

  return (
    <ToolFallbackRoot
      open={open}
      onOpenChange={setUserOpen}
      className={cn(
        isCancelled && "border-muted-foreground/30 bg-muted/30",
        pendingAction && "border-warning/40 bg-warning/5",
      )}
    >
      <ToolFallbackTrigger
        toolName={toolName}
        status={status}
        result={result}
        pendingAction={pendingAction}
      />
      <ToolFallbackContent>
        {pendingAction && actionToken && pendingContext ? (
          <PendingActionApproval
            action={pendingAction}
            processing={processing}
            disabled={Boolean(pendingContext.disabled || pendingContext.processingToken)}
            onConfirm={() => void pendingContext.onConfirm(actionToken)}
            onCancel={() => void pendingContext.onCancel(actionToken)}
          />
        ) : (
          <>
            <ToolFallbackError status={status} />
            <ToolFallbackArgs
              argsText={argsText}
              className={cn(isCancelled && "opacity-60")}
            />
            {!isCancelled && <ToolFallbackResult result={result} />}
          </>
        )}
      </ToolFallbackContent>
    </ToolFallbackRoot>
  );
};

const ToolFallback = memo(
  ToolFallbackImpl,
) as unknown as ToolCallMessagePartComponent & {
  Root: typeof ToolFallbackRoot;
  Trigger: typeof ToolFallbackTrigger;
  Content: typeof ToolFallbackContent;
  Args: typeof ToolFallbackArgs;
  Result: typeof ToolFallbackResult;
  Error: typeof ToolFallbackError;
};

ToolFallback.displayName = "ToolFallback";
ToolFallback.Root = ToolFallbackRoot;
ToolFallback.Trigger = ToolFallbackTrigger;
ToolFallback.Content = ToolFallbackContent;
ToolFallback.Args = ToolFallbackArgs;
ToolFallback.Result = ToolFallbackResult;
ToolFallback.Error = ToolFallbackError;

export {
  ToolFallback,
  ToolFallbackRoot,
  ToolFallbackTrigger,
  ToolFallbackContent,
  ToolFallbackArgs,
  ToolFallbackResult,
  ToolFallbackError,
};
