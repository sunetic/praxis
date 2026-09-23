"""Observe provider wire fields beside native SDK parts without changing responses.

Replays one saved request context, not a whole run. Tool calls are never executed.
Only use isolated synthetic test conversations: reports contain their full text.
Supply the snapshot encryption key via environment; HTTP headers are not recorded.
"""

import argparse
import asyncio
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.direct import model_request_stream
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.services.agent.definitions import CHAT_INSTRUCTIONS
from app.services.agent.models import ModelConnectionConfig, ModelSnapshot
from app.services.datasource.agent_tools import database_tools
from app.services.function.agent_tools import function_tools
from app.services.integration.agent_tools import service_tools
from app.services.knowledge.agent_tools import knowledge_tools
from app.services.page.agent_tools import page_tools
from app.services.skill.agent_tools import skill_draft_tools


class ObservedStream(httpx2.AsyncByteStream):
    def __init__(self, source, sample):
        self.source, self.sample = source, sample
        self.buffer = bytearray()

    async def __aiter__(self):
        async for chunk in self.source:
            self.buffer.extend(chunk)
            yield chunk

    async def aclose(self):
        self.sample["wire_body"] = self.buffer.decode("utf-8", errors="replace")
        await self.source.aclose()


class ObservedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, samples):
        self.samples = samples
        self.transport = httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        sample = {"request": json.loads(request.content), "started_at": time.time()}
        self.samples.append(sample)
        response = await self.transport.handle_async_request(request)
        sample["status_code"] = response.status_code
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=ObservedStream(response.stream, sample),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.transport.aclose()


def wire_summary(body):
    text, reasoning, fields, finishes = [], [], set(), []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].lstrip()
        if data == "[DONE]":
            continue
        value = json.loads(data)
        for choice in value.get("choices", []):
            delta = choice.get("delta", {})
            fields.update(delta)
            if delta.get("content"):
                text.append(delta["content"])
            for field in ("reasoning", "reasoning_content"):
                if delta.get(field):
                    reasoning.append(delta[field])
            if choice.get("finish_reason"):
                finishes.append(choice["finish_reason"])
    return {
        "content": "".join(text),
        "reasoning": "".join(reasoning),
        "delta_fields": sorted(fields),
        "finish_reasons": finishes,
    }


def candidate_config(config: ModelConnectionConfig, model_name: str | None):
    """One explicit diagnostic variable; never mutate platform or run settings."""
    if model_name is None:
        return config
    return ModelConnectionConfig(
        **{**config.model_dump(), "model_name": model_name}, api_key=config.api_key
    )


def conversation_payloads(db: sqlite3.Connection, conversation_id: str, through_seq: int):
    """Load the same cross-run native history boundary used by RunStore."""
    return [
        json.loads(message[0])
        for message in db.execute(
            "select m.payload from agent_messages m "
            "join agent_runs r on r.id=m.run_id "
            "where r.conversation_id=? and r.seq<=? "
            'order by r.seq,m."index"',
            (conversation_id, through_seq),
        )
    ]


async def run(args):
    with sqlite3.connect(f"file:{args.snapshot_db}?mode=ro", uri=True) as db:
        row = db.execute(
            "select model_snapshot,definition,conversation_id,seq from agent_runs where id=?",
            (args.run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Saved run not found")
        original_config = ModelSnapshot.model_validate(json.loads(row[0])).restore()
        config = candidate_config(original_config, args.candidate_model)
        definition = json.loads(row[1])
        payloads = conversation_payloads(db, row[2], row[3])
    messages = ModelMessagesTypeAdapter.validate_python(payloads)
    indices = [index for index, message in enumerate(messages) if isinstance(message, ModelRequest)]
    index = indices[args.request_index]
    messages = messages[: index + 1]
    registered = {
        **database_tools(None),
        **function_tools(None),
        **page_tools(None),
        **knowledge_tools(None),
        **service_tools(None),
        **skill_draft_tools(None),
    }
    parameters = ModelRequestParameters(
        function_tools=[registered[name].tool.tool_def for name in sorted(definition["tool_names"])]
    )
    if args.minimal:
        messages = [
            ModelRequest(
                parts=[UserPromptPart("只用两句话解释数据库索引的作用与代价。")],
                instructions=CHAT_INSTRUCTIONS,
            )
        ]
        parameters = ModelRequestParameters()
    report = {
        "source_run_id": args.run_id,
        "request_index": index,
        "minimal": args.minimal,
        "configuration": config.model_dump(mode="json"),
        "source_model": original_config.model_name,
        "candidate_model_override": args.candidate_model,
        "thinking_mode_override": args.thinking_mode,
        "samples": [],
        "acceptance_complete": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for number in range(args.samples):
            sample = {"sample": number + 1, "http": []}
            report["samples"].append(sample)
            started = time.monotonic()
            client = AsyncOpenAI(
                api_key=config.api_key.get_secret_value(),
                base_url=str(config.base_url),
                timeout=config.timeout_seconds,
                max_retries=config.transport_retries,
                http_client=httpx2.AsyncClient(transport=ObservedTransport(sample["http"])),
            )
            try:
                model = OpenAIChatModel(
                    config.model_name, provider=OpenAIProvider(openai_client=client)
                )
                async with asyncio.timeout(config.timeout_seconds):
                    async with model_request_stream(
                        model,
                        messages,
                        model_settings={
                            **config.request_settings(),
                            **(
                                {"extra_body": {"thinking": {"type": args.thinking_mode}}}
                                if args.thinking_mode
                                else {}
                            ),
                        },
                        model_request_parameters=parameters,
                    ) as stream:
                        async for _ in stream:
                            pass
                        response = stream.get()
                sample["native"] = json.loads(ModelMessagesTypeAdapter.dump_json([response]))[0]
                sample["usage"] = asdict(response.usage)
            except Exception as exc:
                sample["error_type"] = type(exc).__name__
            finally:
                await client.close()
                sample["seconds"] = time.monotonic() - started
                for attempt in sample["http"]:
                    attempt["summary"] = wire_summary(attempt.get("wire_body", ""))
                serialized = json.dumps(report, ensure_ascii=False, indent=2)
                args.output.write_text(
                    serialized.replace(config.api_key.get_secret_value(), "[redacted]")
                )
                print(
                    json.dumps(
                        {
                            "sample": number + 1,
                            "seconds": sample["seconds"],
                            "error_type": sample.get("error_type"),
                            "attempts": [
                                {
                                    "status": attempt["status_code"],
                                    "fields": attempt["summary"]["delta_fields"],
                                    "content_chars": len(attempt["summary"]["content"]),
                                    "reasoning_chars": len(attempt["summary"]["reasoning"]),
                                }
                                for attempt in sample["http"]
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    finally:
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        args.output.write_text(serialized.replace(config.api_key.get_secret_value(), "[redacted]"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-db", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--request-index", type=int, default=-1, help="Index among saved model requests"
    )
    parser.add_argument("--minimal", action="store_true")
    parser.add_argument(
        "--thinking-mode",
        choices=["enabled", "disabled"],
        help="Diagnostic DeepSeek thinking parameter; does not change product configuration",
    )
    parser.add_argument(
        "--candidate-model",
        help="Explicit model-only comparison on the same endpoint; never updates product config",
    )
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
