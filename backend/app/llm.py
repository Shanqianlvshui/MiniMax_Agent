import asyncio
import json
import os
from collections.abc import AsyncIterator

import httpx


class LLMClient:
    async def stream_planner(self, goal: str) -> AsyncIterator[str]:
        raise NotImplementedError


class FakeLLMClient(LLMClient):
    async def stream_planner(self, goal: str) -> AsyncIterator[str]:
        for token in ["计划", "：", goal, "\n", "1. 明确约束和验证标准\n"]:
            await asyncio.sleep(0.01)
            yield token


class MiniMaxAnthropicClient(LLMClient):
    def __init__(self) -> None:
        self.base_url = os.environ.get(
            "MINIMAX_BASE_URL",
            "https://api.minimaxi.com/anthropic",
        )
        self.api_key = os.environ.get("MINIMAX_API_KEY", "")
        self.model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
        self.context_window_tokens = int(
            os.environ.get("MINIMAX_CONTEXT_WINDOW_TOKENS", "1000000")
        )
        self.max_output_tokens = int(
            os.environ.get("MINIMAX_MAX_OUTPUT_TOKENS", "131072")
        )
        self.thinking_type = os.environ.get("MINIMAX_THINKING", "adaptive")

    async def stream_planner(self, goal: str) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError("MiniMax credentials are not configured.")

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = self._planner_payload(goal)

        url = f"{self.base_url.rstrip('/')}/v1/messages"
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line.removeprefix("data:").strip()
                    if not raw or raw == "[DONE]":
                        continue
                    data = json.loads(raw)
                    if data.get("type") != "content_block_delta":
                        continue
                    delta = data.get("delta", {})
                    if delta.get("type") in {"text_delta", "thinking_delta"}:
                        text = delta.get("text") or delta.get("thinking")
                        if text:
                            yield text

    def _planner_payload(self, goal: str) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "stream": True,
            "system": "你是规划 Agent。请用中文输出简洁、可执行、带验证标准的实现计划。",
            "messages": [
                {
                    "role": "user",
                    "content": goal,
                }
            ],
        }
        if self.thinking_type != "disabled":
            payload["thinking"] = {"type": self.thinking_type}
        return payload


def create_llm_client() -> LLMClient:
    if os.environ.get("MINIMAX_AGENT_LLM_MODE", "fake") == "fake":
        return FakeLLMClient()
    return MiniMaxAnthropicClient()
