import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { datasourcesApi, statsAnalysisApi } from "@/lib/api"
import type {
  DataSource,
  StatsIssueItem,
  StatsWorkbenchResponse,
} from "@/lib/api"

type UseStatsAnalysisWorkbenchResult = {
  loadingDatasources: boolean
  loadingWorkbench: boolean
  scopedDatasources: DataSource[]
  clusterOptions: string[]
  selectedClusterKey: string
  selectedDatasourceId: number | null
  selectedIssueId: string | null
  selectedIssue: StatsIssueItem | null
  workbench: StatsWorkbenchResponse | null
  setSelectedClusterKey: (value: string) => void
  setSelectedDatasourceId: (value: number | null) => void
  setSelectedIssueId: (issueId: string | null) => void
  refreshWorkbench: () => void
}

export const ALL_CLUSTERS = "__all__"

export function useStatsAnalysisWorkbench(): UseStatsAnalysisWorkbenchResult {
  const [datasources, setDatasources] = useState<DataSource[]>([])
  const [loadingDatasources, setLoadingDatasources] = useState(false)
  const [selectedClusterKey, setSelectedClusterKey] = useState(ALL_CLUSTERS)
  const [selectedDatasourceId, setSelectedDatasourceId] = useState<number | null>(null)

  const [workbench, setWorkbench] = useState<StatsWorkbenchResponse | null>(null)
  const [loadingWorkbench, setLoadingWorkbench] = useState(false)
  const [refreshCount, setRefreshCount] = useState(0)

  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null)

  const previousScopeRef = useRef<string | null>(null)
  const requestVersionRef = useRef(0)

  const scopedDatasources = useMemo(() => datasources.filter((ds) => ds.db_type === "oceanbase"), [datasources])

  const clusterOptions = useMemo(
    () => Array.from(new Set(scopedDatasources.map((item) => item.cluster_key))).sort(),
    [scopedDatasources]
  )

  const issues = workbench?.issues ?? []
  const selectedIssue = useMemo(
    () => issues.find((item) => item.issue_id === selectedIssueId) ?? null,
    [issues, selectedIssueId]
  )

  useEffect(() => {
    let cancelled = false
    setLoadingDatasources(true)
    datasourcesApi
      .list()
      .then((list) => {
        if (cancelled) return
        setDatasources(list)
      })
      .catch((err) => {
        if (!cancelled) toast.error(`加载数据源失败: ${String(err)}`)
      })
      .finally(() => {
        if (!cancelled) setLoadingDatasources(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (selectedClusterKey === ALL_CLUSTERS) {
      return
    }
    if (!selectedClusterKey) return
    const clusterDatasources = scopedDatasources.filter((item) => item.cluster_key === selectedClusterKey)
    if (!clusterDatasources.length) {
      setSelectedDatasourceId(null)
      return
    }
    // When switching to a specific cluster, clear datasource if it doesn't belong
    setSelectedDatasourceId((current) => {
      if (!current) return null
      return clusterDatasources.some((item) => item.id === current) ? current : null
    })
  }, [scopedDatasources, selectedClusterKey])

  useEffect(() => {
    if (scopedDatasources.length === 0) return
    const effectiveClusterKey = selectedClusterKey === ALL_CLUSTERS ? undefined : selectedClusterKey

    let cancelled = false
    setLoadingWorkbench(true)
    requestVersionRef.current += 1
    const requestVersion = requestVersionRef.current

    const scopeKey = selectedDatasourceId ? `ds:${selectedDatasourceId}` : `ck:${selectedClusterKey}`
    const scopeChanged = previousScopeRef.current != null && previousScopeRef.current !== scopeKey
    if (scopeChanged) {
      setWorkbench(null)
    }
    previousScopeRef.current = scopeKey

    statsAnalysisApi
      .getWorkbench({
        datasource_id: selectedDatasourceId ?? undefined,
        cluster_key: effectiveClusterKey,
        lookback_days: 7,
        stale_days: 7,
      })
      .then((payload) => {
        if (cancelled || requestVersion !== requestVersionRef.current) return
        setWorkbench(payload)
        setSelectedIssueId((currentIssueId) => {
          if (!currentIssueId) return payload.issues[0]?.issue_id ?? null
          return payload.issues.some((item) => item.issue_id === currentIssueId) ? currentIssueId : (payload.issues[0]?.issue_id ?? null)
        })
      })
      .catch((err) => {
        if (!cancelled) {
          toast.error(`加载工作台失败: ${String(err)}`)
          setWorkbench(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingWorkbench(false)
      })

    return () => {
      cancelled = true
    }
  }, [refreshCount, scopedDatasources, selectedDatasourceId, selectedClusterKey])

  return {
    loadingDatasources,
    loadingWorkbench,
    scopedDatasources,
    clusterOptions,
    selectedClusterKey,
    selectedDatasourceId,
    selectedIssueId,
    selectedIssue,
    workbench,
    setSelectedClusterKey,
    setSelectedDatasourceId,
    setSelectedIssueId,
    refreshWorkbench: () => setRefreshCount((value) => value + 1),
  }
}
