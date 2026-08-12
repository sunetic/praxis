import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/attachment";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/reasoning";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  useComposerRuntime,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  MoreHorizontalIcon,
  PencilIcon,
  RefreshCwIcon,
  SquareIcon,
} from "lucide-react";

import type { FC, ReactNode } from "react";
import { useCallback } from "react";
import { useShellI18n } from "@/i18n/shellI18n";

export type ThreadSuggestion = {
  label: string;
  prompt: string;
};

export const Thread: FC<{ suggestions?: ThreadSuggestion[]; footerContent?: ReactNode; isWaiting?: boolean; placeholder?: string; readOnly?: boolean }> = ({
  suggestions = [],
  footerContent,
  isWaiting = false,
  placeholder,
  readOnly = false,
}) => {
  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root @container flex h-full flex-col bg-card"
      style={{
        ["--thread-max-width" as string]: "100%",
        ["--composer-radius" as string]: "12px",
        ["--composer-padding" as string]: "8px",
      }}
    >
      <ThreadPrimitive.Viewport
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-hidden overflow-y-scroll scroll-smooth"
      >
        <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-8 pt-6">
          <AuiIf condition={(s) => s.thread.isEmpty}>
            <ThreadWelcome suggestions={suggestions} />
          </AuiIf>

          <div
            data-slot="aui_message-group"
            className="mb-6 flex flex-col gap-y-6 empty:hidden"
          >
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
            <AuiIf condition={(s) => s.thread.isRunning}>
              {isWaiting && <ThreadThinkingIndicator />}
            </AuiIf>
          </div>

          <ThreadPrimitive.ViewportFooter className="aui-thread-viewport-footer sticky bottom-0 mt-auto flex flex-col gap-3 overflow-visible bg-card pb-5">
            <ThreadScrollToBottom />
            {footerContent}
            {!readOnly ? <Composer placeholder={placeholder} /> : null}
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC = () => {
  const role = useAuiState((s) => s.message.role);
  const isEditing = useAuiState((s) => s.message.composer.isEditing);

  if (isEditing) return <EditComposer />;
  if (role === "user") return <UserMessage />;
  return <AssistantMessage />;
};

const ThreadScrollToBottom: FC = () => {
  const { t } = useShellI18n();
  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <TooltipIconButton
        tooltip={t("thread.scrollToBottom")}
        variant="outline"
        className="aui-thread-scroll-to-bottom absolute -top-10 z-10 self-center rounded-full disabled:invisible"
      >
        <ArrowDownIcon />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC<{ suggestions: ThreadSuggestion[] }> = ({ suggestions }) => {
  const { t } = useShellI18n();
  return (
    <div className="aui-thread-welcome-root my-auto flex grow flex-col">
      <div className="aui-thread-welcome-center flex w-full grow flex-col items-center justify-center">
        <div className="aui-thread-welcome-message flex size-full flex-col justify-center px-2 pb-6">
          <h1 className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both font-semibold text-xl text-foreground duration-200">
            {t("thread.welcome.title")}
          </h1>
          <p className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-sm text-muted-foreground delay-75 duration-200 mt-1">
            {t("thread.welcome.subtitle")}
          </p>
        </div>
      </div>
      {suggestions.length > 0 && <ThreadSuggestions suggestions={suggestions} />}
    </div>
  );
};

const ThreadSuggestions: FC<{ suggestions: ThreadSuggestion[] }> = ({ suggestions }) => {
  return (
    <div className="aui-thread-welcome-suggestions grid w-full @md:grid-cols-2 gap-2 pb-5">
      {suggestions.map((s, i) => (
        <ThreadSuggestionItem key={i} suggestion={s} />
      ))}
    </div>
  );
};

const ThreadSuggestionItem: FC<{ suggestion: ThreadSuggestion }> = ({ suggestion }) => {
  const composer = useComposerRuntime();
  const handleClick = useCallback(() => {
    composer.setText(suggestion.prompt);
    composer.send();
  }, [composer, suggestion.prompt]);

  return (
    <div className="fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <Button
        variant="ghost"
        onClick={handleClick}
        className="h-auto w-full @md:flex-col flex-wrap items-start justify-start gap-1 rounded-xl border border-border bg-card px-4 py-3 text-start text-sm transition-colors hover:bg-muted"
      >
        <span className="font-medium text-foreground text-sm">{suggestion.label}</span>
      </Button>
    </div>
  );
};

const Composer: FC<{ placeholder?: string }> = ({ placeholder }) => {
  const { t } = useShellI18n();
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="flex w-full flex-col gap-2 rounded-(--composer-radius) border border-border bg-card p-(--composer-padding) shadow-sm transition-shadow focus-within:border-ring/75 focus-within:ring-2 focus-within:ring-ring/20 data-[dragging=true]:border-ring data-[dragging=true]:border-dashed data-[dragging=true]:bg-accent/50"
        >
          <ComposerAttachments />
          <ComposerPrimitive.Input
            placeholder={placeholder || t("thread.input.placeholder")}
            className="aui-composer-input max-h-40 min-h-[2.5rem] w-full resize-none bg-transparent px-2 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground/70"
            rows={1}
            autoFocus
            aria-label={t("thread.input.aria")}
          />
          <ComposerAction />
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  );
};

const ComposerAction: FC = () => {
  const { t } = useShellI18n();
  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-between">
      <ComposerAddAttachment />
      <AuiIf condition={(s) => !s.thread.isRunning}>
        <ComposerPrimitive.Send asChild>
          <TooltipIconButton
            tooltip={t("thread.send")}
            side="bottom"
            type="button"
            variant="default"
            size="icon"
            className="aui-composer-send size-8 rounded-full"
            aria-label={t("thread.send.aria")}
          >
            <ArrowUpIcon className="size-4" />
          </TooltipIconButton>
        </ComposerPrimitive.Send>
      </AuiIf>
      <AuiIf condition={(s) => s.thread.isRunning}>
        <ComposerPrimitive.Cancel asChild>
          <Button
            type="button"
            variant="default"
            size="icon"
            className="aui-composer-cancel size-8 rounded-full"
            aria-label={t("thread.stop.aria")}
          >
            <SquareIcon className="size-3 fill-current" />
          </Button>
        </ComposerPrimitive.Cancel>
      </AuiIf>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="mt-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-destructive text-sm">
        <ErrorPrimitive.Message className="line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

const AssistantMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 relative animate-in duration-150"
    >
      {/* Content area — left-aligned, no bubble background */}
      <div
        data-slot="aui_assistant-message-content"
        className="wrap-break-word text-sm text-foreground leading-relaxed space-y-3"
      >
        <MessagePrimitive.GroupedParts
          groupBy={(part) => {
            if (part.type === "reasoning")
              return ["group-chainOfThought", "group-reasoning"];
            if (part.type === "tool-call") return null;
            return null;
          }}
        >
          {({ part, children }) => {
            switch (part.type) {
              case "group-chainOfThought":
                return <div data-slot="aui_chain-of-thought" className="space-y-2">{children}</div>;
              case "group-reasoning": {
                const running = part.status.type === "running";
                return (
                  <ReasoningRoot defaultOpen={running}>
                    <ReasoningTrigger active={running} />
                    <ReasoningContent aria-busy={running}>
                      <ReasoningText>{children}</ReasoningText>
                    </ReasoningContent>
                  </ReasoningRoot>
                );
              }
              case "text":
                return <MarkdownText />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return (
                  <div className="max-w-[560px]">
                    {part.toolUI ?? <ToolFallback {...part} />}
                  </div>
                );
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessageError />
      </div>

      {/* Action bar */}
      <div className="mt-1 flex items-center">
        <AssistantActionBar />
      </div>
    </MessagePrimitive.Root>
  );
};

const AssistantActionBar: FC = () => {
  const { t } = useShellI18n();
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="flex gap-0.5 text-muted-foreground"
    >
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip={t("thread.copy")}>
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <CopyIcon />
          </AuiIf>
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Reload asChild>
        <TooltipIconButton tooltip={t("thread.regenerate")}>
          <RefreshCwIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Reload>
      <ActionBarMorePrimitive.Root>
        <ActionBarMorePrimitive.Trigger asChild>
          <TooltipIconButton
            tooltip={t("thread.more")}
            className="data-[state=open]:bg-accent"
          >
            <MoreHorizontalIcon />
          </TooltipIconButton>
        </ActionBarMorePrimitive.Trigger>
        <ActionBarMorePrimitive.Content
          side="bottom"
          align="start"
          className="z-50 min-w-32 overflow-hidden rounded-lg border border-border bg-card p-1 shadow-md"
        >
          <ActionBarPrimitive.ExportMarkdown asChild>
            <ActionBarMorePrimitive.Item className="flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground">
              <DownloadIcon className="size-4" />
              {t("thread.exportMd")}
            </ActionBarMorePrimitive.Item>
          </ActionBarPrimitive.ExportMarkdown>
        </ActionBarMorePrimitive.Content>
      </ActionBarMorePrimitive.Root>
    </ActionBarPrimitive.Root>
  );
};

const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 grid animate-in auto-rows-auto grid-cols-[minmax(32px,1fr)_minmax(0,90%)] content-start gap-y-1 duration-150 [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="relative col-start-2 flex min-w-0 items-start justify-end">
        {/* User bubble: primary color per design spec §9 */}
        <div className="wrap-break-word peer rounded-xl bg-primary px-4 py-2.5 text-sm text-primary-foreground empty:hidden">
          <MessagePrimitive.Parts />
        </div>
        <div className="absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 peer-empty:hidden rtl:translate-x-full">
          <UserActionBar />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
};

const UserActionBar: FC = () => {
  const { t } = useShellI18n();
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="always"
      className="flex flex-col items-end"
    >
      <ActionBarPrimitive.Edit asChild>
        <TooltipIconButton tooltip={t("thread.edit")} className="p-4">
          <PencilIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Edit>
    </ActionBarPrimitive.Root>
  );
};

const ThreadThinkingIndicator: FC = () => {
  return (
    <div className="flex items-center gap-1.5 px-0.5 text-muted-foreground">
      <span className="size-1.5 rounded-full bg-current animate-bounce [animation-delay:0ms]" />
      <span className="size-1.5 rounded-full bg-current animate-bounce [animation-delay:150ms]" />
      <span className="size-1.5 rounded-full bg-current animate-bounce [animation-delay:300ms]" />
    </div>
  );
};

const EditComposer: FC = () => {
  const { t } = useShellI18n();
  return (
    <MessagePrimitive.Root
      data-slot="aui_edit-composer-wrapper"
      className="flex flex-col"
    >
      <ComposerPrimitive.Root className="ms-auto flex w-full max-w-[90%] flex-col rounded-xl bg-muted">
        <ComposerPrimitive.Input
          className="min-h-14 w-full resize-none bg-transparent p-4 text-foreground text-sm outline-none"
          autoFocus
        />
        <div className="mx-3 mb-3 flex items-center gap-2 self-end">
          <ComposerPrimitive.Cancel asChild>
            <Button variant="ghost" size="sm">{t("thread.editCancel")}</Button>
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send asChild>
            <Button size="sm">{t("thread.editUpdate")}</Button>
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};
