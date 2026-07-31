from __future__ import annotations

import json
from typing import Any

_PAGE_CHART_CONTRACT: dict[str, Any] = {
    "contract_version": "page-chart-contract-v1",
    "design_profile": {
        "style_family": "grafana_like_light",
        "defaults": {
            "grid_stroke": "var(--color-chart-grid)",
            "grid_stroke_opacity": 0.35,
            "line_stroke_width": 2.2,
            "line_dot": False,
            "bar_fill_opacity": 0.78,
            "tooltip": True,
            "legend": True,
            "animation_duration_ms": 900,
        },
    },
    "components": {
        "line_trend": {
            "wrapper_component": "ChartContainer",
            "component": "LineChart",
            "library": "recharts",
            "import_paths": {
                "wrapper": "@/components/ui/chart",
                "primitives": "recharts",
            },
            "description": "Render time-series trends for duration/rate/count metrics.",
            "required_props": ["title", "data", "x_key", "series"],
            "props_schema": {
                "title": {"type": "string", "min_length": 1},
                "data": {"type": "array", "items": {"type": "object"}, "min_items": 1},
                "x_key": {"type": "string", "min_length": 1},
                "series": {
                    "type": "array",
                    "min_items": 1,
                    "items": {
                        "type": "object",
                        "additional_properties": False,
                        "properties": {
                            "key": {"type": "string", "min_length": 1},
                            "label": {"type": "string", "min_length": 1},
                            "color_token": {
                                "type": "string",
                                "enum": ["chart_a", "chart_b", "chart_c", "chart_d"],
                            },
                        },
                        "constraints": {"required": ["key", "label"]},
                    },
                },
                "y_axis": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {
                        "label": {"type": "string"},
                        "min": {"type": "number"},
                        "max": {"type": "number"},
                    },
                },
                "height": {"type": "integer", "minimum": 180, "maximum": 480, "default": 240},
            },
        },
        "bar_grouped": {
            "wrapper_component": "ChartContainer",
            "component": "BarChart",
            "library": "recharts",
            "import_paths": {
                "wrapper": "@/components/ui/chart",
                "primitives": "recharts",
            },
            "description": "Render grouped or stacked bars for cross-group comparisons.",
            "required_props": ["title", "data", "x_key", "series"],
            "props_schema": {
                "title": {"type": "string", "min_length": 1},
                "data": {"type": "array", "items": {"type": "object"}, "min_items": 1},
                "x_key": {"type": "string", "min_length": 1},
                "series": {
                    "type": "array",
                    "min_items": 1,
                    "items": {
                        "type": "object",
                        "additional_properties": False,
                        "properties": {
                            "key": {"type": "string", "min_length": 1},
                            "label": {"type": "string", "min_length": 1},
                            "color_token": {
                                "type": "string",
                                "enum": ["chart_a", "chart_b", "chart_c", "chart_d"],
                            },
                        },
                        "constraints": {"required": ["key", "label"]},
                    },
                },
                "stacked": {"type": "boolean", "default": False},
                "height": {"type": "integer", "minimum": 180, "maximum": 480, "default": 240},
            },
        },
    },
    "render_rules": [
        "Select chart component from components only; never invent unsupported chart types.",
        "Use ChartContainer as wrapper and use the declared recharts primitives only.",
        "Every chart must provide all required_props and valid props_schema types.",
        "Use color_token values only; do not hardcode arbitrary chart colors in contract-bound output.",
    ],
}


def get_page_chart_contract() -> dict[str, Any]:
    return json.loads(json.dumps(_PAGE_CHART_CONTRACT, ensure_ascii=False))


def get_page_chart_contract_json() -> str:
    return json.dumps(_PAGE_CHART_CONTRACT, ensure_ascii=False, sort_keys=True)


def get_page_chart_contract_block() -> str:
    return f"Page Chart Contract (JSON):\n{get_page_chart_contract_json()}"
