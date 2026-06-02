# -*- coding: utf-8 -*-
"""
Provider factory and registry.
"""
from typing import Dict, Optional
from app.agent.providers.base import (
    LLMProvider,
    DeepSeekProvider,
    AnthropicProvider,
    OpenAIProvider,
    SiliconFlowProvider,
)


class ProviderRegistry:
    """Registry for LLM providers."""

    def __init__(self):
        self._providers: Dict[str, type] = {
            "deepseek": DeepSeekProvider,
            "anthropic": AnthropicProvider,
            "openai": OpenAIProvider,
            "siliconflow": SiliconFlowProvider,
        }

    def get_provider(self, name: str, api_key: str, **config) -> LLMProvider:
        """Get a provider instance by name."""
        provider_class = self._providers.get(name.lower())
        if not provider_class:
            raise ValueError(f"Unknown provider: {name}")

        return provider_class(api_key=api_key, **config)

    def register_provider(self, name: str, provider_class: type) -> None:
        """Register a new provider."""
        self._providers[name.lower()] = provider_class

    def list_providers(self) -> list:
        """List available provider names."""
        return list(self._providers.keys())


# Global registry instance
registry = ProviderRegistry()


def get_provider(name: str, api_key: str, **config) -> LLMProvider:
    """Get a provider instance."""
    return registry.get_provider(name, api_key, **config)