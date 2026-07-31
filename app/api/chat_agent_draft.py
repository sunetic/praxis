"""Chat agent-draft endpoints — split from app/api/chat.py for module size."""

import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.services.chat.vds import event_to_vds as _event_to_vds
from app.services.llm import get_llm_client
from app.skills.store import skill_store
from app.tools.registry import registry

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = get_logger("chat.agent_draft")


def _json_dumps_safe(payload: dict) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def _normalize_json_payload(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    return json.loads(_json_dumps_safe(payload))


def _normalize_string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
        if len(normalized) >= limit:
            break
    return normalized


def _json_loads_safe(raw: str) -> dict | None:
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_selector_payload(raw_text: str) -> dict | None:
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
        cleaned = cleaned.strip()
    direct = _json_loads_safe(cleaned)
    if direct:
        return direct
    matched = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if matched:
        return _json_loads_safe(matched.group(0))
    return None


def _extract_latest_active_skills(events: list[models.ChatEvent], fallback: list[str]) -> list[str]:
    for event in reversed(events):
        if event.event_type != "skill_delta":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        active = _normalize_string_list(payload.get("active_skills"), limit=16)
        if active:
            return active
    return _normalize_string_list(fallback, limit=16)


_META_TOOL_NAMES = {"agent_save"}  # lifecycle tools, not domain tools


def _extract_tool_call_trace(messages: list[models.Message], *, limit: int = 16) -> list[dict]:
    trace: list[dict] = []
    for msg in messages:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or "").strip()
            if not name or name in _META_TOOL_NAMES:
                continue
            trace.append(
                {
                    "name": name,
                    "input": tc.get("input") or {},
                    "result": tc.get("result"),
                }
            )
    return trace[-limit:]


def _derive_agent_name_from_seed(seed: str) -> str:
    cleaned = re.sub(r"[。！？.!?]", " ", seed or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "Chat-distilled PraxisAgent"
    short = cleaned[:24].strip()
    if short.lower().endswith("agent"):
        return short
    return f"{short} PraxisAgent"


def _build_agent_draft_fallback(
    *,
    conversation: models.Conversation,
    messages: list[models.Message],
    events: list[models.ChatEvent],
    user_input: str,
) -> dict[str, Any]:
    user_messages = [
        (msg.content or "").strip()
        for msg in messages
        if msg.role == "user" and (msg.content or "").strip()
    ]
    assistant_messages = [
        (msg.content or "").strip()
        for msg in messages
        if msg.role == "assistant" and (msg.content or "").strip()
    ]
    latest_user = user_messages[-1] if user_messages else ""
    seed = user_input or latest_user or conversation.title or ""
    tool_trace = _extract_tool_call_trace(messages, limit=8)
    tool_names = list(dict.fromkeys(tc["name"] for tc in tool_trace))
    active_skills = _extract_latest_active_skills(
        events,
        conversation.active_skills if isinstance(conversation.active_skills, list) else [],
    )
    scenario_lines = [
        f"{idx + 1}. {text[:220]}" for idx, text in enumerate(user_messages[-3:])
    ] or ["1. Handle database operations and troubleshooting requests"]
    workflow_lines = (
        [
            f"{idx + 1}. Call `{name}` and proceed to the next step based on the result."
            for idx, name in enumerate(tool_names)
        ]
        if tool_names
        else [
            "1. Clarify the user's goal and constraints.",
            "2. Select appropriate tools for analysis or execution.",
            "3. Output conclusions, risks, and recommended next steps.",
        ]
    )
    assistant_style = assistant_messages[-1][:500] if assistant_messages else ""
    prompt_lines = [
        "You are a database operations PraxisAgent distilled from real chat sessions.",
        "",
        "## Scenario",
        *scenario_lines,
        "",
        "## Standard Workflow",
        *workflow_lines,
        "",
        "## Constraints",
        "- For write or high-risk operations, you must explain the risks and wait for confirmation first.",
        "- Prioritize actionable steps and clear conclusions in your responses.",
    ]
    if active_skills:
        prompt_lines.extend(["", "## Recommended Skills", ", ".join(active_skills)])
    if assistant_style:
        prompt_lines.extend(["", "## Reference Response Style", assistant_style])

    return {
        "name": _derive_agent_name_from_seed(seed),
        "description": f"Distilled from chat context: {(seed or 'database operations scenario')[:72]}",
        "prompt": "\n".join(prompt_lines),
        "tools": tool_names,
        "skills": active_skills,
    }


async def _build_agent_draft_from_conversation(
    *,
    conversation: models.Conversation,
    messages: list[models.Message],
    events: list[models.ChatEvent],
    user_input: str,
    available_tool_names: set[str],
    available_skill_names: set[str],
) -> dict[str, Any]:
    fallback = _build_agent_draft_fallback(
        conversation=conversation,
        messages=messages,
        events=events,
        user_input=user_input,
    )
    # Exclude the save-trigger message itself — it's a lifecycle command, not a business intent
    save_trigger = user_input.lower()
    user_messages = [
        (msg.content or "").strip()
        for msg in messages
        if msg.role == "user"
        and (msg.content or "").strip()
        and (msg.content or "").strip().lower() != save_trigger
    ]
    assistant_messages = [
        (msg.content or "").strip()
        for msg in messages
        if msg.role == "assistant" and (msg.content or "").strip()
    ]
    tool_call_trace = _extract_tool_call_trace(messages, limit=16)
    active_skills = _extract_latest_active_skills(
        events,
        conversation.active_skills if isinstance(conversation.active_skills, list) else [],
    )
    context_payload = {
        "conversation_title": conversation.title,
        "latest_user_messages": user_messages[-6:],
        "latest_assistant_messages": assistant_messages[-3:],
        "tool_call_trace": tool_call_trace,
        "active_skills": active_skills,
        "datasource_id": conversation.datasource_id,
    }
    llm = get_llm_client()
    system_prompt = (
        "You are extracting the optimal execution path from a chat session to create a reusable PraxisAgent config.\n"
        "The session contains tool_call_trace: a list of tool calls with their inputs and results.\n"
        "Some calls are exploratory (retries, wrong parameters, failed attempts). Identify the FINAL successful path.\n"
        "\n"
        "Rules for workflow section:\n"
        "- Only include the effective steps, not failed attempts\n"
        "- For each step: what tool to call, how to determine its parameters (direct input or derived from previous step result), what result to expect\n"
        "- If a tool was called to discover parameters for another tool, describe that dependency explicitly\n"
        "\n"
        "CRITICAL — generalize all parameter values:\n"
        "- Never hardcode specific IDs, names, timestamps, or numeric values from this session into the prompt\n"
        "- Replace concrete values with descriptions of how to obtain them (e.g. 'cluster ID provided by user' not 'targetId=2')\n"
        "- The generated prompt must work for any user input, not just this specific session\n"
        "\n"
        "Output strict JSON only with keys: name, description, prompt, tools, skills.\n"
        "name <= 48 chars; description <= 180 chars; prompt must include sections: scenario, workflow, constraints.\n"
        "tools/skills must be arrays of strings (tool/skill names only)."
    )
    try:
        response = None
        async for chunk in llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)},
            ],
            tools=None,
            stream=False,
            temperature=0.2,
        ):
            response = chunk
            break
        raw_text = ""
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                raw_text = str(message.get("content") or "")
        parsed = _extract_selector_payload(raw_text) or {}
        parsed_name = str(parsed.get("name") or "").strip()
        parsed_description = str(parsed.get("description") or "").strip()
        parsed_prompt = str(parsed.get("prompt") or "").strip()
        parsed_tools = _normalize_string_list(parsed.get("tools"), limit=16)
        parsed_skills = _normalize_string_list(parsed.get("skills"), limit=16)

        tools = [name for name in parsed_tools if name in available_tool_names]
        if not tools:
            tools = [name for name in fallback["tools"] if name in available_tool_names]

        skills = [name for name in parsed_skills if name in available_skill_names]
        if not skills:
            skills = [name for name in fallback["skills"] if name in available_skill_names]

        return {
            "name": (parsed_name[:48] or fallback["name"]),
            "description": (parsed_description[:180] or fallback["description"]),
            "prompt": parsed_prompt or fallback["prompt"],
            "tools": tools,
            "skills": skills,
        }
    except Exception as exc:
        logger.exception(
            "save_agent_summarize_failed %s error=%s",
            fmt_kv(conversation_id=conversation.id),
            str(exc),
        )
        fallback["tools"] = [name for name in fallback["tools"] if name in available_tool_names]
        fallback["skills"] = [name for name in fallback["skills"] if name in available_skill_names]
        return fallback


async def _stream_save_agent_workflow(
    *,
    conversation_id: int,
    conversation: models.Conversation,
    db: Session,
    user_input: str,
    route_source: str,
):
    messages = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.created_at.asc())
        .all()
    )
    events = (
        db.query(models.ChatEvent)
        .filter(models.ChatEvent.conversation_id == conversation_id)
        .order_by(models.ChatEvent.created_at.asc())
        .all()
    )
    available_tool_names = {
        tool.get("function", {}).get("name")
        for tool in registry.get_openai_functions()
        if isinstance(tool, dict)
    }
    available_tool_names = {
        name for name in available_tool_names if isinstance(name, str) and name.strip()
    }
    available_skill_names = {item.name for item in skill_store.load()}
    trace_id = str(uuid.uuid4())

    try:
        summarizing_payload = {
            "stage": "summarizing_context",
            "message": "Summarizing context...",
            "trace_id": trace_id,
            "route_source": route_source,
        }
        db.add(
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="agent_save",
                phase="summarizing",
                payload=_normalize_json_payload(summarizing_payload),
            )
        )
        db.commit()
        yield _event_to_vds({"type": "save_agent_status", "data": summarizing_payload})

        draft = await _build_agent_draft_from_conversation(
            conversation=conversation,
            messages=messages,
            events=events,
            user_input=user_input,
            available_tool_names=available_tool_names,
            available_skill_names=available_skill_names,
        )

        saving_payload = {
            "stage": "saving_agent",
            "message": "Saving PraxisAgent...",
            "trace_id": trace_id,
            "route_source": route_source,
        }
        db.add(
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="agent_save",
                phase="saving",
                payload=_normalize_json_payload(saving_payload),
            )
        )
        db.commit()
        yield _event_to_vds({"type": "save_agent_status", "data": saving_payload})

        db_agent = models.Agent(
            name=str(draft.get("name") or "Chat-distilled PraxisAgent").strip()[:48],
            description=str(draft.get("description") or "").strip()[:180] or None,
            prompt=str(draft.get("prompt") or "").strip(),
            tools=_normalize_string_list(draft.get("tools"), limit=16),
            skills=_normalize_string_list(draft.get("skills"), limit=16),
            agent_type="custom",
            status="active",
        )
        if not db_agent.prompt:
            raise ValueError("PraxisAgent prompt cannot be empty")
        db.add(db_agent)
        db.flush()

        assistant_notice = (
            f"The current chat has been summarized and saved as PraxisAgent: {db_agent.name}. "
            f"Go to /agent?editAgentId={db_agent.id} to review and edit."
        )
        db.add(
            models.Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_notice,
            )
        )

        done_payload = {
            "stage": "completed",
            "message": "Saved. You can go to the PraxisAgent edit page to continue editing.",
            "agent_id": db_agent.id,
            "agent_name": db_agent.name,
            "agent_type": db_agent.agent_type,
            "agent_url": f"/agent?editAgentId={db_agent.id}",
            "trace_id": trace_id,
            "route_source": route_source,
        }
        db.add(
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="agent_save",
                phase="done",
                payload=_normalize_json_payload(done_payload),
            )
        )
        db.commit()
        db.refresh(db_agent)

        yield _event_to_vds({"type": "save_agent_done", "data": done_payload})
        yield _event_to_vds({"type": "done", "data": {"trace_id": trace_id}})
    except Exception as exc:
        db.rollback()
        logger.exception(
            "save_agent_stream_failed %s error=%s",
            fmt_kv(conversation_id=conversation_id, trace_id=trace_id, route_source=route_source),
            str(exc),
        )
        user_message = "Failed to save PraxisAgent. Please try again."
        try:
            db.add(
                models.ChatEvent(
                    conversation_id=conversation_id,
                    event_type="agent_save",
                    phase="error",
                    payload=_normalize_json_payload(
                        {
                            "stage": "error",
                            "message": str(exc),
                            "trace_id": trace_id,
                            "route_source": route_source,
                        }
                    ),
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        yield (
            "data: "
            + _json_dumps_safe(
                {
                    "type": "error",
                    "data": {
                        "message": str(exc),
                        "user_message": user_message,
                        "error_class": "save_agent_error",
                        "trace_id": trace_id,
                        "route_source": route_source,
                    },
                }
            )
            + "\n\n"
        )


@router.post("/{conversation_id}/save-agent/stream")
async def save_conversation_as_agent_stream(
    conversation_id: int,
    payload: dict | None = None,
    db: Session = Depends(get_db),
):
    conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_input = str((payload or {}).get("user_input") or "").strip()
    return StreamingResponse(
        _stream_save_agent_workflow(
            conversation_id=conversation_id,
            conversation=conversation,
            db=db,
            user_input=user_input,
            route_source="save_agent_endpoint",
        ),
        media_type="text/plain; charset=utf-8",
    )
