from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PREVIEW_THEME_CSS = """
:root{
  --background:#F5F6FA;
  --foreground:#111827;
  --card:#ffffff;
  --muted:#f3f4f6;
  --muted-foreground:#6b7280;
  --primary:#5B5BD6;
  --primary-foreground:#ffffff;
  --border:#e5e7eb;
  --radius:12px;
  --color-positive:#4d7c5a;
  --color-negative:#c75c5c;
  --color-warning:#e5a54b;
  --color-chart-grid:#e5e7eb;
  --color-ink-tertiary:#a8a29e;
}
*{box-sizing:border-box}
html,body{width:100%;min-height:100%}
body{
  margin:0;
  background:var(--background);
  color:var(--foreground);
  font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
  line-height:1.5;
}
main{
  max-width:1320px;
  margin:0 auto;
  padding:24px;
}
.card{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:var(--radius);
  box-shadow:0 1px 2px rgba(15,23,42,.04);
}
button,.btn{
  border:1px solid transparent;
  border-radius:10px;
  padding:10px 14px;
  background:var(--primary);
  color:var(--primary-foreground);
  font:inherit;
  cursor:pointer;
}
button.ghost{
  background:#fff;
  color:var(--foreground);
  border-color:var(--border);
}
input,select,textarea{
  width:100%;
  border:1px solid var(--border);
  border-radius:10px;
  padding:10px 12px;
  background:#fff;
  color:var(--foreground);
}
table{
  width:100%;
  border-collapse:collapse;
  background:#fff;
}
th,td{
  border-bottom:1px solid var(--border);
  text-align:left;
  padding:10px 12px;
}
th{
  font-weight:600;
  background:var(--muted);
}
""".strip()

PREVIEW_LAYOUT_CSS = """
.stack{display:flex;flex-direction:column;gap:16px}
.tabs{display:flex;gap:24px;border-bottom:1px solid var(--border);padding-bottom:8px}
.tab{padding:8px 0;color:var(--muted-foreground);font-weight:600}
.tab.active{color:var(--primary);border-bottom:2px solid var(--primary)}
.filter-row{
  display:grid;
  grid-template-columns:200px 200px 1fr 260px;
  gap:12px;
  align-items:center;
}
.chart-grid{
  display:grid;
  grid-template-columns:3fr 2fr;
  gap:16px;
}
.chart-placeholder{
  height:220px;
  border:1px dashed var(--border);
  border-radius:10px;
  background:linear-gradient(180deg,#fff,var(--muted));
  padding:8px;
}
.chart-placeholder svg{
  width:100%;
  height:100%;
}
.chart-placeholder .series-line{
  stroke-linecap:round;
  animation:line-fade-in .9s ease both;
}
.chart-placeholder .series-area{
  opacity:.26;
  animation:line-fade-in .9s ease both;
}
.chart-placeholder .series-bar{
  transform-origin:center bottom;
  animation:bar-rise .85s ease both;
}
@keyframes line-fade-in{
  from{opacity:0;transform:translateY(4px)}
  to{opacity:1;transform:translateY(0)}
}
@keyframes bar-rise{
  from{opacity:.1;transform:scaleY(.25)}
  to{opacity:1;transform:scaleY(1)}
}
.status-chip{
  display:inline-flex;
  align-items:center;
  gap:6px;
  border-radius:999px;
  padding:2px 8px;
  font-size:12px;
  font-weight:600;
}
.status-warn{background:rgba(229,165,75,0.1);color:var(--color-warning)}
.status-risk{background:rgba(199,92,92,0.1);color:var(--color-negative)}
.status-ok{background:rgba(77,124,90,0.1);color:var(--color-positive)}
.progress{
  height:8px;
  border-radius:999px;
  background:#eef1f5;
  overflow:hidden;
}
.progress > span{
  display:block;
  height:100%;
  border-radius:999px;
}
.page-footer{
  display:flex;
  justify-content:space-between;
  align-items:center;
  padding:12px 4px 0;
  color:var(--muted-foreground);
}
.pagination{
  display:flex;
  align-items:center;
  gap:6px;
}
.page-btn{
  border:1px solid var(--border);
  background:#fff;
  min-width:32px;
  height:32px;
  border-radius:8px;
}
.page-btn.active{
  background:var(--primary);
  color:#fff;
  border-color:var(--primary);
}
.drawer-mask{
  position:fixed;
  inset:0;
  background:rgba(15,23,42,.35);
  display:none;
}
.drawer{
  position:fixed;
  top:0;
  right:0;
  width:min(640px,92vw);
  height:100vh;
  background:#fff;
  border-left:1px solid var(--border);
  box-shadow:-8px 0 24px rgba(15,23,42,.08);
  padding:20px;
  overflow:auto;
  transform:translateX(100%);
  transition:transform .2s ease;
}
.drawer.open{transform:translateX(0)}
.drawer-mask.open{display:block}
.risk-card{
  border:1px solid rgba(199,92,92,0.3);
  background:rgba(199,92,92,0.05);
  border-radius:12px;
  padding:12px;
}
.advice-card{
  border:1px solid rgba(91,91,214,0.3);
  background:rgba(91,91,214,0.03);
  border-radius:12px;
  padding:12px;
}
@media (max-width: 1100px){
  .filter-row{grid-template-columns:1fr 1fr}
}
@media (max-width: 900px){
  .chart-grid{grid-template-columns:1fr}
}
""".strip()

DEFAULT_PAGE_SOURCE_TSX = """
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
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

export default function Page() {
  return (
    <main className="space-y-6">
      <div className="rounded-xl bg-card p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <select className="h-9 rounded-lg border border-border bg-card px-3 text-sm">
              <option>全部集群</option>
              <option>集群A</option>
              <option>集群B</option>
            </select>
            <input placeholder="搜索..." className="h-9 w-64 rounded-lg border border-border bg-card px-3 text-sm" />
          </div>
          <button className="h-9 rounded-lg border border-border bg-card px-4 text-sm font-medium">刷新</button>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="rounded-lg border border-border bg-card p-5 shadow-sm lg:col-span-3">
          <h3 className="mb-4 text-sm font-medium">趋势概览</h3>
          <ChartContainer config={trendChartConfig} className="h-[220px] w-full">
            <AreaChart data={trendData} margin={{ left: 8, right: 8, top: 6, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-chart-grid)" strokeOpacity={0.5} />
              <XAxis dataKey="day" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} />
              <YAxis tickLine={false} axisLine={false} width={32} tick={{ fontSize: 11, fill: "var(--color-ink-tertiary)" }} />
              <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
              <Area dataKey="healthy" type="monotone" stroke="var(--color-healthy)" strokeWidth={2} fill="none" dot={false} />
              <Area dataKey="warning" type="monotone" stroke="var(--color-warning)" strokeWidth={2} fill="none" dot={false} />
              <Area dataKey="critical" type="monotone" stroke="var(--color-critical)" strokeWidth={2} fill="none" dot={false} />
            </AreaChart>
          </ChartContainer>
        </div>
        <div className="rounded-lg border border-border bg-card p-5 shadow-sm lg:col-span-2">
          <h3 className="mb-4 text-sm font-medium">集群分布</h3>
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
      <div className="rounded-xl bg-card shadow-sm">
        <div className="flex items-center justify-between border-b px-5 py-3" style={{ borderColor: "var(--border)" }}>
          <span className="text-sm font-medium">明细列表</span>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b" style={{ borderColor: "var(--border)" }}>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>集群</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>状态</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>对象数</th>
              <th className="px-5 py-3 text-left text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b hover:bg-muted/40" style={{ borderColor: "var(--border)" }}>
              <td className="px-5 py-3">集群A</td>
              <td className="px-5 py-3"><span className="rounded-md px-2 py-0.5 text-xs font-medium" style={{ background: "rgba(77,124,90,0.1)", color: "var(--color-positive)" }}>正常</span></td>
              <td className="px-5 py-3">150</td>
              <td className="px-5 py-3"><button className="text-xs" style={{ color: "var(--primary)" }}>详情</button></td>
            </tr>
          </tbody>
        </table>
        <div className="flex items-center justify-between border-t px-5 py-2" style={{ borderColor: "var(--border)" }}>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>共 1 条</span>
          <div className="flex items-center gap-1">
            <button className="size-8 rounded-md border text-xs" style={{ borderColor: "var(--border)", background: "rgba(91,91,214,0.06)", color: "var(--primary)" }}>1</button>
          </div>
        </div>
      </div>
    </main>
  );
}
""".strip()
DEFAULT_PAGE_SOURCE_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "templates" / "page_baseline_template.tsx"
)


def _escape_html(value: str) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@lru_cache(maxsize=1)
def _load_default_page_source_template() -> str:
    try:
        content = DEFAULT_PAGE_SOURCE_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
        if content:
            return content
    except Exception:
        return DEFAULT_PAGE_SOURCE_TSX
    return DEFAULT_PAGE_SOURCE_TSX


def build_default_page_source_code() -> str:
    return _load_default_page_source_template()


def build_default_page_preview_html(message: str | None = None) -> str:
    note = str(message or "").strip()
    note_block = ""
    if note:
        note_block = (
            "<section class='card' style='padding:12px;color:var(--muted-foreground);margin-bottom:16px;'>"
            f"{_escape_html(note)}"
            "</section>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8' />"
        f"<style id='praxis-preview-theme'>{PREVIEW_THEME_CSS}\n{PREVIEW_LAYOUT_CSS}</style>"
        "</head><body><main>"
        f"{note_block}"
        "<section class='stack'>"
        "<div class='tabs'><span class='tab active'>收集任务</span><span class='tab'>风险检测</span></div>"
        "<div class='filter-row'>"
        "<select><option>全部集群</option><option>集群A</option><option>集群B</option><option>集群C</option></select>"
        "<select><option>全部租户</option><option>租户1</option><option>租户2</option></select>"
        "<input value='' placeholder='关键字（表名 / 风险类型）' />"
        "<input value='03-18 08:52 ~ 03-25 08:52' />"
        "</div>"
        "<div class='chart-grid'>"
        "<section class='card' style='padding:12px;'><h3 style='margin:0 0 10px'>趋势概览（AreaChart）</h3><div class='chart-placeholder'><svg viewBox='0 0 560 220' preserveAspectRatio='none'><defs><linearGradient id='lineA' x1='0' y1='0' x2='0' y2='1'><stop offset='0%' stop-color='var(--primary)' stop-opacity='.38'/><stop offset='100%' stop-color='var(--primary)' stop-opacity='0'/></linearGradient><linearGradient id='lineB' x1='0' y1='0' x2='0' y2='1'><stop offset='0%' stop-color='var(--color-positive)' stop-opacity='.34'/><stop offset='100%' stop-color='var(--color-positive)' stop-opacity='0'/></linearGradient></defs><g fill='none' stroke='var(--border)' stroke-opacity='.45'><path d='M0 190H560'/><path d='M0 140H560'/><path d='M0 90H560'/><path d='M0 40H560'/></g><polygon class='series-area' points='0,88 93,121 186,90 279,145 372,97 465,100 560,125 560,190 0,190' fill='url(#lineA)'/><polygon class='series-area' points='0,118 93,91 186,119 279,140 372,123 465,84 560,123 560,190 0,190' fill='url(#lineB)'/><polyline class='series-line' points='0,88 93,121 186,90 279,145 372,97 465,100 560,125' stroke='var(--primary)' stroke-width='2.4' fill='none'/><polyline class='series-line' points='0,118 93,91 186,119 279,140 372,123 465,84 560,123' stroke='var(--color-positive)' stroke-width='2.4' fill='none'/><polyline class='series-line' points='0,158 93,145 186,139 279,162 372,160 465,156 560,150' stroke='var(--color-warning)' stroke-width='2.4' fill='none'/></svg></div></section>"
        "<section class='card' style='padding:12px;'><h3 style='margin:0 0 10px'>集群分布（BarChart）</h3><div class='chart-placeholder'><svg viewBox='0 0 560 220' preserveAspectRatio='none'><g fill='none' stroke='var(--border)' stroke-opacity='.45'><path d='M0 190H560'/><path d='M0 140H560'/><path d='M0 90H560'/><path d='M0 40H560'/></g><g><rect class='series-bar' x='24' y='150' width='18' height='40' fill='var(--primary)' fill-opacity='.82'/><rect class='series-bar' x='46' y='40' width='18' height='150' fill='var(--color-negative)' fill-opacity='.8'/><rect class='series-bar' x='68' y='128' width='18' height='62' fill='var(--color-positive)' fill-opacity='.8'/><rect class='series-bar' x='104' y='124' width='18' height='66' fill='var(--primary)' fill-opacity='.82'/><rect class='series-bar' x='126' y='54' width='18' height='136' fill='var(--color-negative)' fill-opacity='.8'/><rect class='series-bar' x='148' y='166' width='18' height='24' fill='var(--color-positive)' fill-opacity='.8'/><rect class='series-bar' x='184' y='99' width='18' height='91' fill='var(--primary)' fill-opacity='.82'/><rect class='series-bar' x='206' y='74' width='18' height='116' fill='var(--color-negative)' fill-opacity='.8'/><rect class='series-bar' x='228' y='170' width='18' height='20' fill='var(--color-positive)' fill-opacity='.8'/><rect class='series-bar' x='264' y='98' width='18' height='92' fill='var(--primary)' fill-opacity='.82'/><rect class='series-bar' x='286' y='76' width='18' height='114' fill='var(--color-negative)' fill-opacity='.8'/><rect class='series-bar' x='308' y='172' width='18' height='18' fill='var(--color-positive)' fill-opacity='.8'/><rect class='series-bar' x='344' y='109' width='18' height='81' fill='var(--primary)' fill-opacity='.82'/><rect class='series-bar' x='366' y='159' width='18' height='31' fill='var(--color-negative)' fill-opacity='.8'/><rect class='series-bar' x='388' y='172' width='18' height='18' fill='var(--color-positive)' fill-opacity='.8'/><rect class='series-bar' x='424' y='101' width='18' height='89' fill='var(--primary)' fill-opacity='.82'/><rect class='series-bar' x='446' y='71' width='18' height='119' fill='var(--color-negative)' fill-opacity='.8'/><rect class='series-bar' x='468' y='132' width='18' height='58' fill='var(--color-positive)' fill-opacity='.8'/><rect class='series-bar' x='504' y='130' width='18' height='60' fill='var(--primary)' fill-opacity='.82'/><rect class='series-bar' x='526' y='49' width='18' height='141' fill='var(--color-negative)' fill-opacity='.8'/><rect class='series-bar' x='548' y='165' width='12' height='25' fill='var(--color-positive)' fill-opacity='.8'/></g></svg></div></section>"
        "</div>"
        "<section class='card'>"
        "<table><thead><tr>"
        "<th>集群</th><th>租户</th><th>状态</th><th>失败表数</th><th>成功率</th><th>上次运行</th><th>操作</th>"
        "</tr></thead><tbody>"
        "<tr><td>集群A</td><td>租户1</td><td><span class='status-chip status-warn'>警告</span></td><td>2 / 150</td><td><div class='progress'><span style='width:98.7%;background:var(--color-warning)'></span></div></td><td>02/05 02:00</td><td><button class='ghost' data-open-drawer='1'>诊断</button></td></tr>"
        "<tr><td>集群A</td><td>租户2</td><td><span class='status-chip status-warn'>警告</span></td><td>2 / 500</td><td><div class='progress'><span style='width:99.6%;background:var(--color-positive)'></span></div></td><td>02/05 02:00</td><td><button class='ghost' data-open-drawer='1'>诊断</button></td></tr>"
        "<tr><td>集群B</td><td>租户1</td><td><span class='status-chip status-risk'>异常</span></td><td>2 / 200</td><td><div class='progress'><span style='width:99.0%;background:var(--color-positive)'></span></div></td><td>02/04 02:00</td><td><button class='ghost' data-open-drawer='1'>诊断</button></td></tr>"
        "<tr><td>集群B</td><td>租户2</td><td><span class='status-chip status-risk'>异常</span></td><td>0 / 120</td><td><div class='progress'><span style='width:100%;background:var(--color-positive)'></span></div></td><td>02/03 02:00</td><td><button class='ghost' data-open-drawer='1'>诊断</button></td></tr>"
        "<tr><td>集群A</td><td>租户5</td><td><span class='status-chip status-warn'>警告</span></td><td>5 / 300</td><td><div class='progress'><span style='width:98.3%;background:var(--color-warning)'></span></div></td><td>02/05 02:00</td><td><button class='ghost' data-open-drawer='1'>诊断</button></td></tr>"
        "<tr><td>集群A</td><td>租户6</td><td><span class='status-chip status-risk'>异常</span></td><td>15 / 200</td><td><div class='progress'><span style='width:92.5%;background:var(--color-negative)'></span></div></td><td>02/04 02:00</td><td><button class='ghost' data-open-drawer='1'>诊断</button></td></tr>"
        "</tbody></table>"
        "<div class='page-footer'><span>显示 1 到 10 条，共 14 条</span><div class='pagination'>"
        "<button class='page-btn'>‹</button><button class='page-btn active'>1</button><button class='page-btn'>2</button><button class='page-btn'>›</button>"
        "</div></div>"
        "</section>"
        "</section>"
        "<div class='drawer-mask' id='drawerMask'></div>"
        "<aside class='drawer' id='drawerPanel'>"
        "<h3 style='margin:0'>集群A / 租户1</h3><p style='margin:6px 0 16px;color:var(--muted-foreground)'>统计信息诊断结果</p>"
        "<section class='risk-card'><h4 style='margin:0 0 8px'>检测到的风险</h4><p style='margin:0'>大表收集超时：表行数 >= 100 万且单次收集耗时 > 30 分钟。</p></section>"
        "<section class='advice-card' style='margin-top:12px'><h4 style='margin:0 0 8px'>AI 增强建议</h4><p style='margin:0'>建议对热点列设置更高采样策略，并拆分批量收集窗口。</p></section>"
        "<div style='margin-top:16px;display:flex;justify-content:flex-end;'><button class='ghost' id='closeDrawer'>关闭</button></div>"
        "</aside>"
        "<script>(function(){"
        "const mask=document.getElementById('drawerMask');"
        "const panel=document.getElementById('drawerPanel');"
        "const close=document.getElementById('closeDrawer');"
        "function open(){mask&&mask.classList.add('open');panel&&panel.classList.add('open');}"
        "function hide(){mask&&mask.classList.remove('open');panel&&panel.classList.remove('open');}"
        "document.querySelectorAll('[data-open-drawer]').forEach((el)=>el.addEventListener('click',open));"
        "if(mask){mask.addEventListener('click',hide);} if(close){close.addEventListener('click',hide);}"
        "})();</script>"
        "</main></body></html>"
    )


def ensure_page_preview_theme(html: str) -> str:
    normalized = str(html or "").strip()
    if not normalized:
        return build_default_page_preview_html()
    if "<html" not in normalized.lower():
        escaped = _escape_html(normalized)
        return build_default_page_preview_html(escaped)
    if not re.search(r"<head\b", normalized, flags=re.IGNORECASE):
        normalized = re.sub(
            r"<html([^>]*)>",
            r"<html\1><head></head>",
            normalized,
            count=1,
            flags=re.IGNORECASE,
        )
    if not re.search(r"<meta[^>]+charset", normalized, flags=re.IGNORECASE):
        normalized = re.sub(
            r"<head([^>]*)>",
            r"<head\1><meta charset='utf-8' />",
            normalized,
            count=1,
            flags=re.IGNORECASE,
        )
    if "praxis-preview-theme" not in normalized.lower():
        normalized = re.sub(
            r"</head\s*>",
            f"<style id='praxis-preview-theme'>{PREVIEW_THEME_CSS}\n{PREVIEW_LAYOUT_CSS}</style></head>",
            normalized,
            count=1,
            flags=re.IGNORECASE,
        )
    return normalized
