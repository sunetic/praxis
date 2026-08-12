import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { Loader2, Minus, Plus } from "lucide-react"
import { isAxiosError } from "axios"
import { toast } from "sonner"
import { motion } from "framer-motion"

import { ChatThreadView } from "@/components/chat/ChatThreadView"
import { useChatController } from "@/components/chat/useChatController"
import { buildConversationContext, type BuildChatMessageLike } from "@/lib/buildChatRuntime"
import { PagePreviewRenderer } from "@/components/page/PagePreviewRenderer"
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { pagesApi, type Message, type SceneAgentPayload } from "@/lib/api"
import { isImeComposing } from "@/lib/keyboard"

const BUILD_CANVAS_WIDTH = 1366
const BUILD_CANVAS_HEIGHT = 900
const MIN_PREVIEW_SCALE = 50
const MAX_PREVIEW_SCALE = 140

const PUBLISH_TOAST_ID = "page-publish-status"

function isGenericBuildSummary(text: string): boolean {
  const normalized = text.trim().replace(/\s+/g, "")
  if (!normalized) return true
  return ["页面已更新", "页面草稿已更新。", "页面草稿已更新", "已完成页面更新。", "已完成页面更新"].includes(normalized)
}

function hasDuplicateAssistantSinceLastUser(messages: Message[], nextText: string): boolean {
  const normalized = nextText.trim().replace(/\s+/g, " ")
  if (!normalized) return true
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const item = messages[index]
    if (item.role === "user") break
    if (item.role !== "assistant") continue
    const current = String(item.content || "").trim().replace(/\s+/g, " ")
    if (current === normalized) return true
  }
  return false
}

function buildPageScenePayload(page: any | null): SceneAgentPayload | undefined {
  if (!page) return undefined
  return {
    key: "page_build",
    context: {
      page: "page-console",
      page_id: page.id,
      name: page.name || null,
      status: page.status || null,
    },
    focus_object: {
      kind: "page",
      page_id: page.id,
      name: page.name || null,
      status: page.status || null,
    },
  }
}

export function PageConsolePage() {
  const { pageId } = useParams()
  const navigate = useNavigate()

  const [pages, setPages] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [savingTitle, setSavingTitle] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [titleInput, setTitleInput] = useState("")
  const [runSummary, setRunSummary] = useState("")
  const [previewScalePercent, setPreviewScalePercent] = useState(85)
  const [previewScaleTouched, setPreviewScaleTouched] = useState(false)

  const selectedWorkspacePageId = Number(pageId)
  const loadedChatPageIdRef = useRef<number | null>(null)
  const previewViewportRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    pagesApi
      .list()
      .then((data) => {
        if (!cancelled) setPages(Array.isArray(data) ? data : [])
      })
      .catch(() => {
        if (!cancelled) toast.error("加载 Page 失败")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [pageId])

  const selectedPage = useMemo(
    () => pages.find((item) => item.id === selectedWorkspacePageId) || null,
    [pages, selectedWorkspacePageId]
  )

  const handleBuildStreamDone = (donePayload: Record<string, unknown>) => {
    const nextPage = donePayload.page && typeof donePayload.page === "object" ? donePayload.page : null
    if (nextPage) {
      setPages((prev) => prev.map((item) => (item.id === selectedWorkspacePageId ? nextPage : item)))
    }

    const run = donePayload.build_run && typeof donePayload.build_run === "object" ? donePayload.build_run : {}
    const events = Array.isArray((run as any).events) ? (run as any).events : []
    const applyEvent = [...events].reverse().find((event: any) => String(event?.phase || "") === "apply")
    const applyPayload =
      applyEvent && typeof applyEvent.payload === "object" && applyEvent.payload
        ? applyEvent.payload
        : {}
    const diffSummary = String((applyPayload as any)?.diff_summary || "").trim()
    const rawFinalSummary = String(
      (run as any).result_summary ||
      (run as any).error_summary ||
      donePayload.build_summary ||
      donePayload.assistant_message ||
      "页面已更新"
    )
    const finalSummary =
      isGenericBuildSummary(rawFinalSummary) && diffSummary
        ? diffSummary
        : rawFinalSummary
    setRunSummary(finalSummary)

    const runStatus = String((run as any).status || "").toLowerCase()
    if (runStatus === "failed") {
      toast.error("构建失败，请继续描述调整方向")
    } else if (runStatus === "needs_clarification") {
      toast("需要补充信息后再继续构建")
    } else {
      toast.success("页面已更新")
    }
  }

  const chatController = useChatController({
    title: "Build Chat",
    datasourceId: null,
    sceneAgentPayload: buildPageScenePayload(selectedPage),
    builderScope: selectedPage
      ? {
          scopeObjectType: "page",
          scopeObjectId: String(selectedPage.id),
        }
      : undefined,
    conversationContextBuilder: (messages, nextInput) => buildConversationContext(messages as BuildChatMessageLike[], nextInput, 10),
    onStreamDone: handleBuildStreamDone,
  })
  const setChatMessages = chatController.setMessages

  useEffect(() => {
    if (loading) return
    if (pageId) return
    // No pageId means user accessed /page/workspace without an ID — redirect to list
    navigate("/page", { replace: true })
  }, [loading, pageId, navigate])

  useEffect(() => {
    if (!selectedPage?.name) return
    setTitleInput(selectedPage.name)
  }, [selectedPage?.id, selectedPage?.name])

  useEffect(() => {
    if (!selectedPage) return
    if (loadedChatPageIdRef.current === selectedPage.id) return
    loadedChatPageIdRef.current = selectedPage.id
    let cancelled = false

    const hydrateFromHistory = (history: any[]) => {
      const hydrated: Message[] = []
      history.forEach((item: any, idx: number) => {
        const prompt = String(item?.prompt || "").trim()
        const summary = String(item?.summary || "").trim()
        if (prompt) {
          hydrated.push({
            id: idx * 2 + 1,
            conversation_id: 0,
            role: "user",
            content: prompt,
            created_at: "",
          })
        }
        if (summary) {
          hydrated.push({
            id: idx * 2 + 2,
            conversation_id: 0,
            role: "assistant",
            content: summary,
            agent_name: "PageChatAgent",
            created_at: "",
          })
        }
      })
      if (hydrated.length === 0) {
        hydrated.push({
          id: Date.now(),
          conversation_id: 0,
          role: "assistant",
          content: "描述页面需求，我会边构建边更新预览。",
          agent_name: "PageChatAgent",
          created_at: new Date().toISOString(),
        })
      }
      return hydrated
    }

    const draftHistory = Array.isArray(selectedPage?.draft_payload?.meta?.history)
      ? selectedPage?.draft_payload?.meta?.history
      : []

    pagesApi
      .listBuildRuns(selectedPage.id, 30)
      .then((runs) => {
        if (cancelled) return
        const normalizedRuns = Array.isArray(runs) ? runs : []
        if (normalizedRuns.length > 0) {
          const buildHistory = normalizedRuns
            .slice()
            .reverse()
            .map((run: any) => ({
              prompt: String(run?.prompt || "").trim(),
              summary: String(run?.result_summary || run?.error_summary || "").trim(),
            }))
            .filter((item) => item.prompt || item.summary)
          setChatMessages(hydrateFromHistory(buildHistory))
          return
        }
        setChatMessages(hydrateFromHistory(draftHistory))
      })
      .catch(() => {
        if (cancelled) return
        setChatMessages(hydrateFromHistory(draftHistory))
      })

    return () => {
      cancelled = true
    }
  }, [setChatMessages, selectedPage?.id])

  useEffect(() => {
    if (previewScaleTouched) return
    const viewport = previewViewportRef.current
    if (!viewport) return

    const recomputeScale = () => {
      const width = viewport.clientWidth
      const height = viewport.clientHeight
      if (!width || !height) return
      // Reserve small breathing room to avoid first-screen clipping.
      const widthRatio = (width - 24) / BUILD_CANVAS_WIDTH
      const heightRatio = (height - 24) / BUILD_CANVAS_HEIGHT
      const fitRatio = Math.min(widthRatio, heightRatio)
      if (!Number.isFinite(fitRatio) || fitRatio <= 0) return
      const next = Math.max(MIN_PREVIEW_SCALE, Math.min(MAX_PREVIEW_SCALE, Math.floor(fitRatio * 100)))
      setPreviewScalePercent(next)
    }

    recomputeScale()

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(() => recomputeScale())
      observer.observe(viewport)
      return () => observer.disconnect()
    }

    const onResize = () => recomputeScale()
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [previewScaleTouched, selectedPage?.id])

  useEffect(() => {
    const normalized = runSummary.trim()
    if (!normalized) return
    chatController.setMessages((prev) => {
      if (hasDuplicateAssistantSinceLastUser(prev, normalized)) return prev
      return [
        ...prev,
        {
          id: Date.now(),
          conversation_id: chatController.conversationId ?? 0,
          role: "assistant",
          content: normalized,
          agent_name: "PageChatAgent",
          created_at: new Date().toISOString(),
        },
      ]
    })
  }, [chatController, runSummary])

  const persistTitle = async () => {
    if (!selectedPage) return
    const normalized = titleInput.trim()
    if (!normalized || normalized === selectedPage.name) return
    setSavingTitle(true)
    try {
      const updated = await pagesApi.update(selectedPage.id, { name: normalized })
      setPages((prev) => prev.map((item) => (item.id === selectedPage.id ? updated : item)))
      window.dispatchEvent(new Event("page-navigation-refresh"))
    } catch {
      toast.error("保存 Page 名称失败")
      setTitleInput(selectedPage.name || "")
    } finally {
      setSavingTitle(false)
    }
  }

  const handlePublish = async () => {
    if (!selectedPage || publishing || chatController.streaming) return
    setPublishing(true)
    try {
      const snapshot = await pagesApi.freeze(selectedPage.id, { summary: runSummary || "页面定稿" })
      const compileRun = await pagesApi.compile(selectedPage.id, { snapshot_id: Number(snapshot.id) })
      const compileStatus = String(compileRun.status || "failed")
      if (compileStatus !== "done") {
        toast.error(String(compileRun.error_summary || "编译失败，请稍后重试"), {
          id: PUBLISH_TOAST_ID,
        })
        return
      }
      await pagesApi.publish(selectedPage.id)
      setPages((prev) =>
        prev.map((item) => (item.id === selectedPage.id ? { ...item, status: "published" } : item))
      )
      window.dispatchEvent(new Event("page-navigation-refresh"))
      toast.success("Page 已发布", {
        id: PUBLISH_TOAST_ID,
      })
      navigate(`/page/${selectedPage.id}`)
    } catch (error) {
      const detail = isAxiosError(error)
        ? String((error.response?.data as any)?.detail || "")
        : ""
      toast.error(detail || "发布失败，请稍后重试", {
        id: PUBLISH_TOAST_ID,
      })
    } finally {
      setPublishing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[calc(100vh-8rem)] items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        加载 Page...
      </div>
    )
  }

  if (!selectedPage) {
    return (
      <div className="flex min-h-[calc(100vh-8rem)] items-center justify-center text-muted-foreground">
        暂无可编辑的 Page 草稿
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="flex h-[calc(100vh-8rem)] flex-col gap-3 bg-background p-2 md:p-3"
    >
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink asChild>
              <Link to="/page">Pages</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{selectedPage?.name || `Page ${pageId}`}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[3fr_1.2fr]">
        <motion.section
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.45 }}
          className="relative flex min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm"
        >
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-3">
            <div className="flex min-w-0 items-center gap-2">
              <Input
                value={titleInput}
                onChange={(event) => setTitleInput(event.target.value)}
                onBlur={persistTitle}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !isImeComposing(event)) {
                    event.currentTarget.blur()
                  }
                }}
                className="h-7 max-w-xs border-transparent bg-transparent px-0 text-sm font-semibold text-foreground shadow-none focus-visible:ring-0"
                placeholder="Page 名称"
              />
              {savingTitle ? <Loader2 className="size-3.5 animate-spin text-muted-foreground" /> : null}
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-2 py-1">
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                onClick={() => {
                  setPreviewScaleTouched(true)
                  setPreviewScalePercent((prev) => Math.max(MIN_PREVIEW_SCALE, prev - 5))
                }}
                aria-label="缩小预览"
              >
                <Minus className="size-3.5" />
              </Button>
              <input
                type="range"
                min={MIN_PREVIEW_SCALE}
                max={MAX_PREVIEW_SCALE}
                step={5}
                value={previewScalePercent}
                onChange={(event) => {
                  setPreviewScaleTouched(true)
                  setPreviewScalePercent(Number(event.target.value))
                }}
                className="h-1.5 w-28 accent-primary"
                aria-label="预览缩放"
              />
              <span className="w-12 text-right text-xs font-medium text-muted-foreground">{previewScalePercent}%</span>
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                onClick={() => {
                  setPreviewScaleTouched(true)
                  setPreviewScalePercent((prev) => Math.min(MAX_PREVIEW_SCALE, prev + 5))
                }}
                aria-label="放大预览"
              >
                <Plus className="size-3.5" />
              </Button>
            </div>
          </div>
          <div ref={previewViewportRef} className="min-h-0 flex-1 overflow-auto bg-muted/30 p-4">
            <PagePreviewRenderer
              draft={selectedPage.draft_payload || {}}
              canvasWidth={BUILD_CANVAS_WIDTH}
              canvasHeight={BUILD_CANVAS_HEIGHT}
              scalePercent={previewScalePercent}
            />
          </div>
        </motion.section>

        <motion.aside
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.45, delay: 0.05 }}
          className="flex min-h-0 flex-col rounded-xl border border-border bg-card shadow-sm"
        >
          <ChatThreadView
            controller={chatController}
            title="Build Chat"
            placeholder="描述你要构建的页面，我会逐步生成并更新预览"
            embedded
            showHeader
            enableSaveAsAgent={false}
            enableHandoff={false}
            enableBatchActions={false}
            className="flex-1"
            headerAction={(
              <Button size="sm" onClick={handlePublish} disabled={publishing || chatController.streaming}>
                {publishing ? <Loader2 className="size-4 animate-spin" /> : null}
                发布
              </Button>
            )}
          />
        </motion.aside>
      </div>
    </motion.div>
  )
}
