from app.llm import MiniMaxAnthropicClient


def test_minimax_m3_payload_uses_large_context_defaults(monkeypatch):
    monkeypatch.delenv("MINIMAX_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("MINIMAX_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("MINIMAX_THINKING", raising=False)

    client = MiniMaxAnthropicClient()

    payload = client._agent_payload(
        "planner",
        "你是 Planner。",
        "验证 1M 上下文配置",
    )

    assert client.context_window_tokens == 1_000_000
    assert payload["model"] == "MiniMax-M3"
    assert payload["max_tokens"] == 131_072
    assert payload["thinking"] == {"type": "adaptive"}


def test_thinking_can_be_disabled(monkeypatch):
    monkeypatch.setenv("MINIMAX_THINKING", "disabled")

    client = MiniMaxAnthropicClient()

    payload = client._agent_payload(
        "planner",
        "你是 Planner。",
        "不启用 thinking",
    )

    assert "thinking" not in payload


def test_payload_tracks_agent_metadata(monkeypatch):
    monkeypatch.setenv("MINIMAX_CONTEXT_WINDOW_TOKENS", "1000000")

    client = MiniMaxAnthropicClient()

    payload = client._agent_payload(
        "reviewer",
        "你是 Reviewer。",
        "检查硬件证据。",
    )

    assert payload["system"] == "你是 Reviewer。"
    assert payload["messages"] == [{"role": "user", "content": "检查硬件证据。"}]
    assert "metadata" not in payload
    assert client.context_window_tokens == 1_000_000
