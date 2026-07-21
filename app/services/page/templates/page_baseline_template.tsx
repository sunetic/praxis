import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  XAxis,
  YAxis,
} from "recharts";

/* ── Sample data ────────────────────────────────────────────────────────────── */

const trendData = [
  { day: "04/07", healthy: 162, warning: 18, critical: 4 },
  { day: "04/08", healthy: 175, warning: 14, critical: 6 },
  { day: "04/09", healthy: 158, warning: 22, critical: 3 },
  { day: "04/10", healthy: 180, warning: 11, critical: 5 },
  { day: "04/11", healthy: 171, warning: 19, critical: 7 },
  { day: "04/12", healthy: 145, warning: 8, critical: 2 },
  { day: "04/13", healthy: 138, warning: 6, critical: 1 },
];

const distributionData = [
  { name: "集群A", success: 120, failed: 8 },
  { name: "集群B", success: 98, failed: 14 },
  { name: "集群C", success: 75, failed: 5 },
  { name: "集群D", success: 60, failed: 3 },
];

const trendChartConfig = {
  healthy: { label: "正常", color: "var(--color-chart-1)" },
  warning: { label: "警告", color: "var(--color-chart-3)" },
  critical: { label: "异常", color: "var(--color-chart-5)" },
} satisfies ChartConfig;

const distributionChartConfig = {
  success: { label: "成功", color: "var(--color-chart-1)" },
  failed: { label: "失败", color: "var(--color-chart-5)" },
} satisfies ChartConfig;

const tableData = [
  { cluster: "集群A", tenant: "租户1", status: "warning", objects: 150, failedCount: 2, rate: 98.7, updatedAt: "04/13 02:00" },
  { cluster: "集群A", tenant: "租户2", status: "healthy", objects: 500, failedCount: 0, rate: 100, updatedAt: "04/13 02:00" },
  { cluster: "集群B", tenant: "租户1", status: "critical", objects: 200, failedCount: 12, rate: 94.0, updatedAt: "04/12 02:00" },
  { cluster: "集群B", tenant: "租户2", status: "healthy", objects: 120, failedCount: 0, rate: 100, updatedAt: "04/12 02:00" },
  { cluster: "集群C", tenant: "租户1", status: "warning", objects: 300, failedCount: 5, rate: 98.3, updatedAt: "04/13 02:00" },
  { cluster: "集群C", tenant: "租户2", status: "critical", objects: 200, failedCount: 15, rate: 92.5, updatedAt: "04/12 02:00" },
];

const statusConfig: Record<string, { label: string; dotColor: string; badgeBg: string; badgeText: string }> = {
  healthy: { label: "正常", dotColor: "var(--color-positive, #4d7c5a)", badgeBg: "rgba(77,124,90,0.1)", badgeText: "var(--color-positive, #4d7c5a)" },
  warning: { label: "警告", dotColor: "var(--color-warning, #e5a54b)", badgeBg: "rgba(229,165,75,0.1)", badgeText: "var(--color-warning, #e5a54b)" },
  critical: { label: "异常", dotColor: "var(--color-negative, #c75c5c)", badgeBg: "rgba(199,92,92,0.1)", badgeText: "var(--color-negative, #c75c5c)" },
};

/* ── Page ────────────────────────────────────────────────────────────────────── */

export default function Page() {
  return (
    <main className="space-y-6">
      {/* ── Filter Toolbar ──────────────────────────────────────────────────── */}
      <div className="rounded-xl bg-card p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <select className="h-9 rounded-lg border border-border bg-card px-3 text-sm">
              <option>全部集群</option>
              <option>集群A</option>
              <option>集群B</option>
              <option>集群C</option>
            </select>
            <select className="h-9 rounded-lg border border-border bg-card px-3 text-sm">
              <option>全部租户</option>
              <option>租户1</option>
              <option>租户2</option>
            </select>
            <input
              placeholder="搜索..."
              className="h-9 w-64 rounded-lg border border-border bg-card px-3 text-sm placeholder:text-muted-foreground"
            />
          </div>
          <button className="h-9 rounded-lg border border-border bg-card px-4 text-sm font-medium hover:bg-muted/40">
            刷新
          </button>
        </div>
      </div>

      {/* ── Charts (3/5 + 2/5) ──────────────────────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="rounded-lg border border-border bg-card p-5 shadow-sm lg:col-span-3">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-medium">趋势概览</h3>
            <div className="flex items-center gap-3 text-xs" style={{ color: "var(--muted-foreground)" }}>
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full" style={{ background: "var(--color-chart-1)" }} />
                正常
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full" style={{ background: "var(--color-chart-3)" }} />
                警告
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full" style={{ background: "var(--color-chart-5)" }} />
                异常
              </span>
            </div>
          </div>
          <ChartContainer config={trendChartConfig} className="h-[220px] w-full">
            <AreaChart data={trendData} margin={{ left: 8, right: 8, top: 6, bottom: 0 }}>
              <defs>
                <linearGradient id="fillHealthy" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-healthy)" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="var(--color-healthy)" stopOpacity={0.01} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-chart-grid)" strokeOpacity={0.5} />
              <XAxis dataKey="day" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} />
              <YAxis tickLine={false} axisLine={false} width={32} tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} />
              <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
              <Area dataKey="healthy" type="monotone" stroke="var(--color-healthy)" strokeWidth={2} fill="url(#fillHealthy)" dot={false} />
              <Area dataKey="warning" type="monotone" stroke="var(--color-warning)" strokeWidth={2} fill="none" dot={false} />
              <Area dataKey="critical" type="monotone" stroke="var(--color-critical)" strokeWidth={2} fill="none" dot={false} />
            </AreaChart>
          </ChartContainer>
        </div>
        <div className="rounded-lg border border-border bg-card p-5 shadow-sm lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-medium">集群分布</h3>
          </div>
          <ChartContainer config={distributionChartConfig} className="h-[220px] w-full">
            <BarChart data={distributionData} margin={{ left: 8, right: 8, top: 6, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-chart-grid)" strokeOpacity={0.5} />
              <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} />
              <YAxis tickLine={false} axisLine={false} width={32} tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} />
              <ChartTooltip content={<ChartTooltipContent indicator="dot" />} />
              <ChartLegend content={<ChartLegendContent />} />
              <Bar dataKey="success" fill="var(--color-success)" fillOpacity={0.8} radius={[4, 4, 0, 0]} />
              <Bar dataKey="failed" fill="var(--color-failed)" fillOpacity={0.8} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ChartContainer>
        </div>
      </div>

      {/* ── Table Card ──────────────────────────────────────────────────────── */}
      <div className="rounded-xl bg-card shadow-sm">
        <div className="flex items-center justify-between border-b px-5 py-3" style={{ borderColor: "var(--border)" }}>
          <div className="flex items-center gap-4">
            <span className="text-sm font-medium">明细列表</span>
            <span className="text-xs" style={{ color: "var(--muted-foreground)", fontVariantNumeric: "tabular-nums" }}>
              {tableData.length} 项结果
            </span>
          </div>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b" style={{ borderColor: "var(--border)" }}>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>集群</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>租户</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>状态</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>对象数</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>成功率</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>更新时间</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {tableData.map((row, i) => {
              const st = statusConfig[row.status];
              return (
                <tr key={i} className="border-b transition-colors duration-150 hover:bg-muted/40" style={{ borderColor: "var(--border)" }}>
                  <td className="px-5 py-3">{row.cluster}</td>
                  <td className="px-5 py-3">{row.tenant}</td>
                  <td className="px-5 py-3">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="inline-block size-1.5 rounded-full" style={{ background: st.dotColor }} />
                      <span className="rounded-md px-2 py-0.5 text-xs font-medium" style={{ background: st.badgeBg, color: st.badgeText }}>
                        {st.label}
                      </span>
                    </span>
                  </td>
                  <td className="px-5 py-3" style={{ fontVariantNumeric: "tabular-nums" }}>{row.objects}</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-14 overflow-hidden rounded-full" style={{ background: "var(--muted, #f3f4f6)" }}>
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${row.rate}%`,
                            background: row.rate >= 99 ? "var(--color-positive, #4d7c5a)" : row.rate >= 95 ? "var(--color-warning, #e5a54b)" : "var(--color-negative, #c75c5c)",
                          }}
                        />
                      </div>
                      <span className="text-xs" style={{ fontVariantNumeric: "tabular-nums" }}>{row.rate}%</span>
                    </div>
                  </td>
                  <td className="px-5 py-3 text-xs" style={{ color: "var(--muted-foreground)" }}>{row.updatedAt}</td>
                  <td className="px-5 py-3">
                    <button className="rounded-md px-2 py-1 text-xs font-medium hover:bg-muted/40" style={{ color: "var(--color-primary, #5B5BD6)" }}>
                      详情
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="flex items-center justify-between border-t px-5 py-2" style={{ borderColor: "var(--border)" }}>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>显示 1 到 6 条，共 6 条</span>
          <div className="flex items-center gap-1">
            <button className="size-8 rounded-md border text-xs hover:bg-muted/40" style={{ borderColor: "var(--border)" }}>‹</button>
            <button className="size-8 rounded-md border text-xs font-medium" style={{ borderColor: "var(--color-primary, #5B5BD6)", color: "var(--color-primary, #5B5BD6)", background: "rgba(91,91,214,0.06)" }}>1</button>
            <button className="size-8 rounded-md border text-xs hover:bg-muted/40" style={{ borderColor: "var(--border)" }}>›</button>
          </div>
        </div>
      </div>
    </main>
  );
}
