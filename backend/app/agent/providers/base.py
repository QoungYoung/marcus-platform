# -*- coding: utf-8 -*-
"""
LLM Provider base class and implementations.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]
    stop_reason: Optional[str]
    usage: Dict[str, Any]
    error: Optional[str] = None


@dataclass
class LLMMessage:
    role: str
    content: str


class LLMProvider(ABC):
    """Base class for LLM providers."""

    def __init__(self, api_key: str, **kwargs):
        self.api_key = api_key
        self.extra_config = kwargs

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send chat request to LLM."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Get the default model name."""
        pass


class DeepSeekProvider(LLMProvider):
    """DeepSeek API provider."""

    def __init__(self, api_key: str, api_host: str = "api.siliconflow.cn", model: str = "deepseek-ai/DeepSeek-V4-Flash", **kwargs):
        super().__init__(api_key, **kwargs)
        self.api_host = api_host
        self.default_model = model

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send chat request to DeepSeek API."""
        import httpx

        url = f"https://{self.api_host}/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                return LLMResponse(
                    content=None,
                    tool_calls=None,
                    stop_reason=None,
                    usage={},
                    error=f"API error: {response.status_code} - {response.text}",
                )

            result = response.json()
            choice = result["choices"][0]
            message = choice["message"]

            return LLMResponse(
                content=message.get("content"),
                tool_calls=message.get("tool_calls"),
                stop_reason=choice.get("finish_reason"),
                usage=result.get("usage", {}),
            )

        except Exception as e:
            return LLMResponse(
                content=None,
                tool_calls=None,
                stop_reason=None,
                usage={},
                error=str(e),
            )

    def get_model_name(self) -> str:
        return self.default_model


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514", **kwargs):
        super().__init__(api_key, **kwargs)
        self.default_model = model

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send chat request to Anthropic API."""
        import httpx

        url = "https://api.anthropic.com/v1/messages"

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        # Convert messages format for Anthropic
        anthropic_messages = []
        system_content = None

        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content")
            else:
                anthropic_messages.append({
                    "role": msg.get("role"),
                    "content": msg.get("content"),
                })

        payload = {
            "model": model or self.default_model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if system_content:
            payload["system"] = system_content

        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                return LLMResponse(
                    content=None,
                    tool_calls=None,
                    stop_reason=None,
                    usage={},
                    error=f"API error: {response.status_code} - {response.text}",
                )

            result = response.json()
            content_blocks = result.get("content", [])

            text_content = None
            tool_calls = None

            for block in content_blocks:
                if block.get("type") == "text":
                    text_content = block.get("text")
                elif block.get("type") == "tool_use":
                    if tool_calls is None:
                        tool_calls = []
                    tool_calls.append({
                        "id": block.get("id"),
                        "function": {
                            "name": block.get("name"),
                            "arguments": block.get("input"),
                        },
                    })

            return LLMResponse(
                content=text_content,
                tool_calls=tool_calls,
                stop_reason=result.get("stop_reason"),
                usage={
                    "input_tokens": result.get("usage", {}).get("input_tokens"),
                    "output_tokens": result.get("usage", {}).get("output_tokens"),
                },
            )

        except Exception as e:
            return LLMResponse(
                content=None,
                tool_calls=None,
                stop_reason=None,
                usage={},
                error=str(e),
            )

    def get_model_name(self) -> str:
        return self.default_model


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", **kwargs):
        super().__init__(api_key, **kwargs)
        self.default_model = model

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send chat request to OpenAI API."""
        import httpx

        url = "https://api.openai.com/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                return LLMResponse(
                    content=None,
                    tool_calls=None,
                    stop_reason=None,
                    usage={},
                    error=f"API error: {response.status_code} - {response.text}",
                )

            result = response.json()
            choice = result["choices"][0]
            message = choice["message"]

            return LLMResponse(
                content=message.get("content"),
                tool_calls=message.get("tool_calls"),
                stop_reason=choice.get("finish_reason"),
                usage=result.get("usage", {}),
            )

        except Exception as e:
            return LLMResponse(
                content=None,
                tool_calls=None,
                stop_reason=None,
                usage={},
                error=str(e),
            )

    def get_model_name(self) -> str:
        return self.default_model


class SiliconFlowProvider(DeepSeekProvider):
    """SiliconFlow API - compatible with DeepSeek API."""
    pass