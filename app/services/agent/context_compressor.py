"""
Context compressor for agent message histories.

Provides token-aware compression: protects head (system + first exchange)
and tail (most recent messages), summarizes the middle via LLM.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.platform.prompt_loader import PromptLoader

logger = get_logger("agent.context_compressor")


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token per ~4 characters."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(str(block.get("text") or block.get("content") or ""))
                elif isinstance(block, str):
                    total += estimate_tokens(block)
        # tool_calls contribute tokens too
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = tc.get("function") or {}
                total += estimate_tokens(str(fn.get("name") or ""))
                total += estimate_tokens(str(fn.get("arguments") or ""))
    return total


class ContextCompressor:
    """
    Compresses conversation history when it exceeds a token threshold.

    Strategy:
      1. Protect head messages (system prompt + first user/assistant exchange)
      2. Protect tail messages (most recent messages within tail_budget_tokens)
      3. Summarize middle messages via LLM (or simple extraction if no LLM)
    """

    def __init__(
        self,
        *,
        threshold_tokens: int = 60_000,
        tail_budget_tokens: int = 20_000,
        head_messages_count: int = 3,
        llm_client: Any | None = None,
    ) -> None:
        self.threshold_tokens = threshold_tokens
        self.tail_budget_tokens = tail_budget_tokens
        self.head_messages_count = head_messages_count
        self._llm = llm_client

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        """Check if messages exceed the compression threshold."""
        return estimate_messages_tokens(messages) >= self.threshold_tokens

    async def compress(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Compress messages by summarizing the middle section.

        Returns a new message list: head + [summary] + tail.
        """
        total_tokens = estimate_messages_tokens(messages)
        if total_tokens < self.threshold_tokens:
            return list(messages)

        # Split into head / middle / tail
        head = messages[: self.head_messages_count]
        rest = messages[self.head_messages_count :]

        # Find tail: walk backwards until we hit tail_budget_tokens
        tail_tokens = 0
        tail_start_idx = len(rest)
        for i in range(len(rest) - 1, -1, -1):
            msg_tokens = estimate_messages_tokens([rest[i]])
            if tail_tokens + msg_tokens > self.tail_budget_tokens:
                break
            tail_tokens += msg_tokens
            tail_start_idx = i

        middle = rest[:tail_start_idx]
        tail = rest[tail_start_idx:]

        if not middle:
            return list(messages)

        logger.info(
            "context_compress head=%d middle=%d tail=%d total_tokens=%d",
            len(head),
            len(middle),
            len(tail),
            total_tokens,
        )

        summary_text = await self._summarize_messages(middle)
        summary_message: dict[str, Any] = {
            "role": "system",
            "content": (
                f"[Context Summary — {len(middle)} earlier messages compressed]\n\n"
                f"{summary_text}"
            ),
        }

        return head + [summary_message] + tail

    async def _summarize_messages(self, messages: list[dict[str, Any]]) -> str:
        """Summarize a list of messages. Uses LLM if available, else extracts key content."""
        if self._llm is not None:
            return await self._summarize_with_llm(messages)
        return self._summarize_extractive(messages)

    async def _summarize_with_llm(self, messages: list[dict[str, Any]]) -> str:
        """Use the LLM to produce a concise summary of the conversation segment."""
        conversation_text = self._format_messages_for_summary(messages)

        summary_messages = [
            {
                "role": "system",
                "content": PromptLoader.render("agent/prompts/context_compressor.tpl"),
            },
            {
                "role": "user",
                "content": f"Summarize this conversation segment:\n\n{conversation_text}",
            },
        ]

        try:
            response: dict[str, Any] | None = None
            async for chunk in self._llm.chat(
                messages=summary_messages,
                tools=None,
                stream=False,
                temperature=0.1,
            ):
                response = chunk
                break

            if response is None:
                return self._summarize_extractive(messages)

            content = (
                ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ).strip()
            if not content:
                return self._summarize_extractive(messages)

            logger.info("context_compress_llm_summary len=%d", len(content))
            return content
        except Exception as exc:
            logger.warning("context_compress_llm_failed error=%s", str(exc))
            return self._summarize_extractive(messages)

    @staticmethod
    def _summarize_extractive(messages: list[dict[str, Any]]) -> str:
        """Simple extractive summary: take first line of each message."""
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if isinstance(content, str):
                first_line = content.split("\n")[0][:200]
            elif isinstance(content, list):
                first_line = str(content[0])[:200] if content else ""
            else:
                first_line = str(content)[:200]
            if first_line.strip():
                lines.append(f"- [{role}] {first_line.strip()}")

            # Also note tool calls
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or "unknown"
                    lines.append(f"- [{role}] called tool: {name}")

        return "\n".join(lines[-30:])  # Keep last 30 items max

    @staticmethod
    def _format_messages_for_summary(messages: list[dict[str, Any]]) -> str:
        """Format messages into readable text for the summarizer LLM."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if isinstance(content, str):
                text = content[:1000]
            elif isinstance(content, list):
                text = " ".join(str(b)[:200] for b in content[:5])
            else:
                text = str(content)[:1000]

            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                tc_summary = ", ".join(
                    (tc.get("function") or {}).get("name") or "?"
                    for tc in tool_calls[:5]
                )
                text += f"\n[Tool calls: {tc_summary}]"

            parts.append(f"[{role}]: {text}")

        # Cap total to avoid blowing up the summarizer context
        combined = "\n\n".join(parts)
        if len(combined) > 12000:
            combined = combined[:12000] + "\n\n[... truncated ...]"
        return combined
