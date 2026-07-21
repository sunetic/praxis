"use client";

import { memo, useEffect, useRef } from "react";
import { makeAssistantToolUI } from "@assistant-ui/react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { LoaderIcon } from "lucide-react";
import { THEME_NAME } from "@/lib/echarts-theme";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  TitleComponent,
  CanvasRenderer,
]);

const ChartRenderer = memo(function ChartRenderer({
  option,
  title,
}: {
  option: Record<string, unknown>;
  title?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    if (!chartRef.current) {
      chartRef.current = echarts.init(containerRef.current, THEME_NAME);
    }
    chartRef.current.setOption(option, true);

    const ro = new ResizeObserver(() => {
      chartRef.current?.resize();
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [option]);

  return (
    <div className="w-full rounded-lg border border-border bg-card p-4">
      {title && (
        <p className="mb-2 text-sm font-medium text-foreground">{title}</p>
      )}
      <div ref={containerRef} style={{ height: 320, width: "100%" }} />
    </div>
  );
});

export const RenderChartToolUI = makeAssistantToolUI<
  { option: Record<string, unknown>; title?: string },
  { success: boolean; data?: { option: Record<string, unknown>; title?: string }; error?: unknown }
>({
  toolName: "render_chart",
  render: ({ args, result, status }) => {
    if (status.type === "running") {
      return (
        <div className="flex w-full items-center gap-2 rounded-lg border border-border bg-card px-4 py-8">
          <LoaderIcon className="size-4 animate-spin text-muted-foreground" />
          <span className="text-sm text-muted-foreground">Rendering chart...</span>
        </div>
      );
    }

    if (result && !result.success) {
      return (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
          <p className="text-sm text-destructive">
            Chart render failed: {typeof result.error === "string" ? result.error : JSON.stringify(result.error)}
          </p>
        </div>
      );
    }

    const option = result?.data?.option ?? args?.option;
    const title = result?.data?.title ?? args?.title;

    if (!option) return null;

    return <ChartRenderer option={option} title={title} />;
  },
});
