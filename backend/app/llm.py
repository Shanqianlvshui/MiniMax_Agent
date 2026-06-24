import asyncio
import json
import os
from collections.abc import AsyncIterator

import httpx


class LLMClient:
    async def stream_agent(
        self,
        agent_name: str,
        system_prompt: str,
        user_content: str,
    ) -> AsyncIterator[str]:
        raise NotImplementedError


class FakeLLMClient(LLMClient):
    async def stream_agent(
        self,
        agent_name: str,
        system_prompt: str,
        user_content: str,
    ) -> AsyncIterator[str]:
        del system_prompt
        for token in [
            f"{agent_name}：",
            "已读取共享工作流状态。\n",
            user_content[:120],
            "\n",
            "输出：已完成本 Agent 的最小可审计结果。\n",
        ]:
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

    async def stream_agent(
        self,
        agent_name: str,
        system_prompt: str,
        user_content: str,
    ) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError("MiniMax credentials are not configured.")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = self._agent_payload(agent_name, system_prompt, user_content)

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
                    if delta.get("type") == "text_delta":
                        text = delta.get("text")
                        if text:
                            yield text

    def _agent_payload(
        self,
        agent_name: str,
        system_prompt: str,
        user_content: str,
    ) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "stream": True,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": user_content,
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
