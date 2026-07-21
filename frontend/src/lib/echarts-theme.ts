import * as echarts from "echarts/core";

function getColor(varName: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(varName)
      .trim() || fallback
  );
}

function buildTheme(): object {
  return {
    color: [
      getColor("--chart-1", "#5B5BD6"),
      getColor("--chart-2", "#f59e0b"),
      getColor("--chart-3", "#10b981"),
      getColor("--chart-4", "#ec4899"),
      getColor("--chart-5", "#8b5cf6"),
    ],
    backgroundColor: "transparent",
    textStyle: {
      fontFamily: "Inter, system-ui, sans-serif",
      color: getColor("--foreground", "#111827"),
    },
    categoryAxis: {
      axisLine: { lineStyle: { color: getColor("--border", "#e5e7eb") } },
      axisTick: { show: false },
      axisLabel: { color: getColor("--muted-foreground", "#6b7280"), fontSize: 11 },
      splitLine: { show: false },
    },
    valueAxis: {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: getColor("--muted-foreground", "#6b7280"), fontSize: 11 },
      splitLine: {
        lineStyle: {
          color: getColor("--border", "#e5e7eb"),
          type: "dashed",
          opacity: 0.6,
        },
      },
    },
    line: {
      smooth: true,
      symbolSize: 4,
      lineStyle: { width: 2 },
    },
    bar: {
      barMaxWidth: 32,
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    },
    pie: {
      itemStyle: { borderWidth: 2, borderColor: getColor("--card", "#ffffff") },
    },
    tooltip: {
      backgroundColor: getColor("--card", "#ffffff"),
      borderColor: getColor("--border", "#e5e7eb"),
      textStyle: {
        color: getColor("--foreground", "#111827"),
        fontSize: 12,
      },
      borderWidth: 1,
      padding: [8, 12],
    },
    legend: {
      textStyle: { color: getColor("--muted-foreground", "#6b7280"), fontSize: 12 },
    },
    grid: {
      left: 12,
      right: 12,
      top: 32,
      bottom: 12,
      containLabel: true,
    },
  };
}

export const THEME_NAME = "praxis";

export function registerPraxisTheme() {
  echarts.registerTheme(THEME_NAME, buildTheme());
}
