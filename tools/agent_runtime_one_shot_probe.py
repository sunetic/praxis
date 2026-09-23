"""Compare official direct request transports using an isolated saved model snapshot.

Supply the snapshot's encryption key via environment. No credentials are logged.
This is a diagnostic harness, not a product fallback or a model repair loop.
"""

import argparse
import asyncio
import json
import sqlite3
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.direct import model_request_stream
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.output import OutputObjectDefinition

from app.services.agent import models as native_models
from app.services.agent.models import ModelFactory, ModelSnapshot
from app.services.scheduler.builder import SchedulerBuilderService
from app.services.scheduler.triggers import cron_trigger


async def run(database: Path, output: Path, temperature: float | None, output_mode: str):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        snapshot = ModelSnapshot.model_validate(
            json.loads(db.execute("select model_snapshot from agent_runs limit 1").fetchone()[0])
        )
    samples = []
    original = native_models.model_request
    configuration = snapshot.restore()
    if temperature is not None:
        configuration = configuration.model_copy(update={"temperature": temperature})
    factory = ModelFactory(lambda: configuration)
    try:
        for mode in ("nonstream", "stream"):
            for index in range(3):
                sample = {
                    "mode": mode,
                    "sample": index,
                    "temperature": configuration.temperature,
                    "output_mode": output_mode,
                }
                samples.append(sample)

                async def observe(*args, **kwargs):
                    parameters = kwargs["model_request_parameters"]
                    if output_mode != "tool":
                        kwargs["model_request_parameters"] = ModelRequestParameters(
                            output_mode=output_mode,
                            output_object=OutputObjectDefinition(
                                json_schema=parameters.output_tools[0].parameters_json_schema
                            ),
                        )
                    if mode == "nonstream":
                        response = await original(*args, **kwargs)
                    else:
                        async with model_request_stream(*args, **kwargs) as stream:
                            async for _ in stream:
                                pass
                            response = stream.get()
                    sample.update(
                        text=response.text,
                        finish_reason=response.finish_reason,
                        usage=asdict(response.usage),
                        parts=[
                            {
                                "kind": part.part_kind,
                                "length": len(str(getattr(part, "content", ""))),
                            }
                            for part in response.parts
                        ],
                    )
                    if output_mode == "tool":
                        calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
                        sample["output_calls"] = [
                            {"name": part.tool_name, "arguments": part.args_as_json_str()}
                            for part in calls
                        ]
                    else:
                        # Diagnostic-only adapter: let the existing domain validator
                        # inspect text JSON unchanged. No output tool is dispatched.
                        response = replace(
                            response, parts=[ToolCallPart("return_result", response.text or "")]
                        )
                    return response

                native_models.model_request = observe
                started = time.monotonic()
                try:
                    result = await SchedulerBuilderService(factory).apply_prompt(
                        "每个工作日按上海时间上午九点半运行，先保持暂停，不要重试。",
                        {
                            "target_type": "agent",
                            "schedule_type": "cron",
                            "cron_expression": "0 9 * * *",
                            "interval_seconds": None,
                            "timezone": "Asia/Shanghai",
                            "status": "paused",
                            "max_retries": 0,
                        },
                    )
                    sample.update(valid=True, patch=result.patch)
                    effective = {
                        "status": "paused",
                        "max_retries": 0,
                        "timezone": "Asia/Shanghai",
                        **result.patch,
                    }
                    next_run = cron_trigger(
                        effective.get("cron_expression", ""), effective["timezone"]
                    ).get_next_fire_time(None, datetime(2026, 9, 18, 2, tzinfo=UTC))
                    sample["semantic_match"] = (
                        next_run == datetime(2026, 9, 21, 1, 30, tzinfo=UTC)
                        and effective["status"] == "paused"
                        and effective["max_retries"] == 0
                    )
                except Exception as exc:
                    sample.update(valid=False, error_type=type(exc).__name__)
                sample["seconds"] = time.monotonic() - started
                output.write_text(json.dumps(samples, ensure_ascii=False, indent=2))
                print(json.dumps(sample, ensure_ascii=False), flush=True)
    finally:
        native_models.model_request = original
        await factory.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temperature", type=float, choices=[0.0, 0.2, 0.5, 1.0])
    parser.add_argument("--output-mode", choices=["prompted", "native", "tool"], default="prompted")
    args = parser.parse_args()
    asyncio.run(run(args.snapshot_db, args.output, args.temperature, args.output_mode))
