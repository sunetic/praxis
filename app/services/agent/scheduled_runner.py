from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import models
from app.services.scheduler.result import ScheduleRuntimeResult

logger = get_logger('agent.runtime')


class ScheduledAgentRunner:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | Any,
        chat_agent: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        # chat_agent kept for interface compatibility but no longer used
        self._chat_agent = chat_agent

    async def invoke(
        self,
        *,
        agent: models.Agent,
        prompt: str,
        trace_id: str | None = None,
        timeout_seconds: float = 300.0,
        datasource_id: int | None = None,
    ) -> ScheduleRuntimeResult:
        db = self._session_factory()
        try:
            agent_ref = db.query(models.Agent).filter(models.Agent.id == agent.id).first()
            if agent_ref is None:
                return ScheduleRuntimeResult('', 'failed', None, None, 'validation', f'Agent {agent.id} not found', 0)

            effective_prompt = str(prompt or '').strip()

            default_datasource_id = datasource_id
            if default_datasource_id is None and agent_ref.datasources:
                default_datasource_id = agent_ref.datasources[0].id

            conversation = models.Conversation(
                title=f'Scheduler Agent Run - {agent_ref.name}',
                agent_id=agent_ref.id,
                datasource_id=default_datasource_id,
                active_skills=agent_ref.skills if isinstance(agent_ref.skills, list) else None,
                category='scheduler_run',
            )
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
            conversation_id = conversation.id
        except Exception as exc:
            logger.exception('agent_scheduler_create_conversation_failed error=%s', str(exc))
            return ScheduleRuntimeResult('', 'failed', None, None, 'runtime', str(exc), 0)
        finally:
            db.close()

        # Delegate to the chat stream endpoint via ASGI transport (in-process, no network).
        from app.main import app as asgi_app

        stream_url = f'/api/v1/chat/{conversation_id}/stream'

        assistant_chunks: list[str] = []
        error_payload: dict[str, Any] | None = None
        started = asyncio.get_running_loop().time()

        try:
            transport = httpx.ASGITransport(app=asgi_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url='http://internal',
                timeout=timeout_seconds,
            ) as client:
                async with client.stream(
                    'POST',
                    stream_url,
                    json={'content': effective_prompt},
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        return ScheduleRuntimeResult(
                            run_id=str(conversation_id),
                            status='failed',
                            output=None,
                            output_summary=None,
                            error_class='http_error',
                            error_message=f'Stream endpoint returned {response.status_code}: {body[:200]}',
                            duration_ms=int((asyncio.get_running_loop().time() - started) * 1000),
                            conversation_id=conversation_id,
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith('data: '):
                            continue
                        raw = line[6:]
                        if raw in ('[DONE]', ''):
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        event_type = str(event.get('type') or '')
                        if event_type == 'assistant' and str(event.get('phase') or '') == 'responding':
                            text = str((event.get('data') or {}).get('text') or '')
                            if text:
                                assistant_chunks.append(text)
                        elif event_type == 'error' and isinstance(event.get('data'), dict):
                            error_payload = event['data']
        except asyncio.TimeoutError:
            return ScheduleRuntimeResult(
                run_id=str(conversation_id),
                status='failed',
                output=None,
                output_summary=None,
                error_class='timeout',
                error_message='Agent invocation timed out',
                duration_ms=int((asyncio.get_running_loop().time() - started) * 1000),
                conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.exception('agent_scheduler_runtime_failed error=%s', str(exc))
            return ScheduleRuntimeResult(
                run_id=str(conversation_id),
                status='failed',
                output=None,
                output_summary=None,
                error_class='runtime',
                error_message=str(exc),
                duration_ms=int((asyncio.get_running_loop().time() - started) * 1000),
                conversation_id=conversation_id,
            )

        duration_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        if error_payload:
            return ScheduleRuntimeResult(
                run_id=str(conversation_id),
                status='failed',
                output=None,
                output_summary=None,
                error_class=str(error_payload.get('error_class') or 'runtime'),
                error_message=str(error_payload.get('message') or 'Agent runtime error'),
                duration_ms=duration_ms,
                conversation_id=conversation_id,
            )

        assistant_text = ''.join(assistant_chunks).strip() or 'Agent run finished.'
        return ScheduleRuntimeResult(
            run_id=str(conversation_id),
            status='success',
            output={'assistant_message': assistant_text, 'conversation_id': conversation_id},
            output_summary=assistant_text,
            error_class=None,
            error_message=None,
            duration_ms=duration_ms,
            conversation_id=conversation_id,
        )
