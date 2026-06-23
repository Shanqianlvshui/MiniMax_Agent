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
        for token in ["Plan", ": ", goal, "\n", "1. Clarify constraints\n"]:
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

    async def stream_planner(self, goal: str) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError("MiniMax credentials are not configured.")

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "stream": True,
            "system": "You are the Planner agent. Produce a concise implementation plan.",
            "messages": [
                {
                    "role": "user",
                    "content": goal,
                }
            ],
        }

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


def create_llm_client() -> LLMClient:
    if os.environ.get("MINIMAX_AGENT_LLM_MODE", "fake") == "fake":
        return FakeLLMClient()
    return MiniMaxAnthropicClient()
