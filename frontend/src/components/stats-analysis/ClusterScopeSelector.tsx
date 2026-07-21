import type { DataSource } from "@/lib/api"
import { ALL_CLUSTERS } from "./useStatsAnalysisWorkbench"

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

const ALL_DATASOURCES = "__all_datasources__"

type ClusterScopeSelectorProps = {
  clusterOptions: string[]
  datasources: DataSource[]
  clusterValue: string
  datasourceValue: number | null
  disabled?: boolean
  onClusterChange: (value: string) => void
  onDatasourceChange: (value: number | null) => void
}

export function ClusterScopeSelector({
  clusterOptions,
  datasources,
  clusterValue,
  datasourceValue,
  disabled,
  onClusterChange,
  onDatasourceChange,
}: ClusterScopeSelectorProps) {
  const activeDatasources =
    clusterValue === ALL_CLUSTERS
      ? datasources
      : datasources.filter((item) => item.cluster_key === clusterValue)

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <Select
        value={clusterValue}
        disabled={disabled}
        onValueChange={onClusterChange}
      >
        <SelectTrigger aria-label="集群范围" className="w-44 bg-card">
          <SelectValue placeholder="全部集群" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_CLUSTERS}>全部集群</SelectItem>
          {clusterOptions.map((item) => (
            <SelectItem key={item} value={item}>
              {item}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={datasourceValue == null ? ALL_DATASOURCES : String(datasourceValue)}
        disabled={disabled || activeDatasources.length === 0}
        onValueChange={(v) => onDatasourceChange(v === ALL_DATASOURCES ? null : Number(v))}
      >
        <SelectTrigger aria-label="数据源范围" className="w-56 bg-card">
          <SelectValue placeholder="全部数据源" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_DATASOURCES}>全部数据源</SelectItem>
          {activeDatasources.map((item) => (
            <SelectItem key={item.id} value={String(item.id)}>
              {item.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
