import pytest

from app.services.agent.context_compressor import (
    ContextCompressor,
    estimate_messages_tokens,
    estimate_tokens,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_estimate_tokens_basic():
    assert estimate_tokens("") == 1
    assert estimate_tokens("hello world") == max(1, len("hello world") // 4)
    assert estimate_tokens("a" * 400) == 100


def test_estimate_messages_tokens():
    messages = [
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 200},
    ]
    tokens = estimate_messages_tokens(messages)
    assert tokens == 75  # 25 + 50


def test_estimate_messages_tokens_with_tool_calls():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "execute_sql", "arguments": '{"sql": "SELECT 1"}'}}
            ],
        }
    ]
    tokens = estimate_messages_tokens(messages)
    assert tokens > 0


def test_should_compress_below_threshold():
    compressor = ContextCompressor(threshold_tokens=1000)
    messages = [{"role": "user", "content": "short message"}]
    assert compressor.should_compress(messages) is False


def test_should_compress_above_threshold():
    compressor = ContextCompressor(threshold_tokens=100)
    messages = [{"role": "user", "content": "x" * 1000}]
    assert compressor.should_compress(messages) is True


@pytest.mark.anyio
async def test_compress_below_threshold_returns_copy():
    compressor = ContextCompressor(threshold_tokens=100_000)
    messages = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hello"},
    ]
    result = await compressor.compress(messages)
    assert result == messages
    assert result is not messages


@pytest.mark.anyio
async def test_compress_extractive_when_no_llm():
    compressor = ContextCompressor(
        threshold_tokens=50,
        tail_budget_tokens=10,
        head_messages_count=1,
        llm_client=None,
    )
    messages = [
        {"role": "system", "content": "system prompt " + "x" * 200},
        {"role": "user", "content": "first question " + "y" * 200},
        {"role": "assistant", "content": "first answer " + "z" * 200},
        {"role": "user", "content": "second question " + "w" * 200},
        {"role": "assistant", "content": "second answer " + "v" * 200},
        {"role": "user", "content": "latest question"},
    ]
    result = await compressor.compress(messages)

    # Head (1 msg) + summary (1 msg) + tail
    assert result[0]["role"] == "system"
    assert result[0]["content"] == messages[0]["content"]

    # Summary should exist
    summary_msg = result[1]
    assert summary_msg["role"] == "system"
    assert "[Context Summary" in summary_msg["content"]

    # Tail should include the latest message
    assert result[-1]["content"] == "latest question"


@pytest.mark.anyio
async def test_compress_with_fake_llm():
    class FakeSummarizerLLM:
        async def chat(self, messages, tools=None, stream=False, temperature=None):
            yield {"choices": [{"message": {"content": "- Key finding A\n- Decision B made"}}]}

    compressor = ContextCompressor(
        threshold_tokens=50,
        tail_budget_tokens=10,
        head_messages_count=1,
        llm_client=FakeSummarizerLLM(),
    )
    messages = [
        {"role": "system", "content": "system " + "x" * 200},
        {"role": "user", "content": "q1 " + "y" * 200},
        {"role": "assistant", "content": "a1 " + "z" * 200},
        {"role": "user", "content": "q2 " + "w" * 200},
        {"role": "assistant", "content": "a2 " + "v" * 200},
        {"role": "user", "content": "latest"},
    ]
    result = await compressor.compress(messages)

    summary = result[1]
    assert "Key finding A" in summary["content"]
    assert "Decision B" in summary["content"]


@pytest.mark.anyio
async def test_compress_llm_failure_falls_back_to_extractive():
    class FailingLLM:
        async def chat(self, messages, tools=None, stream=False, temperature=None):
            raise RuntimeError("LLM unavailable")
            yield  # pragma: no cover

    compressor = ContextCompressor(
        threshold_tokens=50,
        tail_budget_tokens=10,
        head_messages_count=1,
        llm_client=FailingLLM(),
    )
    messages = [
        {"role": "system", "content": "sys " + "x" * 200},
        {"role": "user", "content": "question one " + "y" * 200},
        {"role": "assistant", "content": "answer one " + "z" * 200},
        {"role": "user", "content": "final"},
    ]
    result = await compressor.compress(messages)

    summary = result[1]
    assert "[Context Summary" in summary["content"]
    # Should be extractive (bullet points with role)
    assert "[user]" in summary["content"] or "[assistant]" in summary["content"]


def test_extractive_summary_includes_tool_calls():
    messages = [
        {
            "role": "assistant",
            "content": "Let me check",
            "tool_calls": [{"function": {"name": "execute_sql", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "1", "content": '{"success": true}'},
    ]
    summary = ContextCompressor._summarize_extractive(messages)
    assert "execute_sql" in summary
