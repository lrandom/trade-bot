"""bot/llm/providers/base.py
---------------------------
Abstract base class that every LLM provider must implement.
"""

from abc import ABC, abstractmethod

from bot.llm.models import LLMResponse


class BaseLLMProvider(ABC):
    """Uniform interface for all LLM backends (Anthropic, OpenAI, Gemini, Deepseek)."""

    @abstractmethod
    async def complete(self, system: str, user: str) -> LLMResponse:
        """Free-form text completion.

        Used for analysis steps (HTF / MTF / LTF) where the response is
        plain text that will be parsed with regex.

        Args:
            system: System prompt.
            user:   User message content.

        Returns:
            LLMResponse with .text populated; .tool_data is empty dict.
        """
        ...

    @abstractmethod
    async def complete_structured(
        self, system: str, user: str, tool_schema: dict
    ) -> LLMResponse:
        """Structured output via tool_use / function_calling.

        Used for signal generation and trade management steps where a
        guaranteed JSON structure is required.

        Args:
            system:      System prompt.
            user:        User message content.
            tool_schema: Canonical tool schema (``parameters`` key format).
                         The provider implementation is responsible for
                         adapting it to its own wire format via
                         ``get_tool_for_provider()``.

        Returns:
            LLMResponse with .tool_data populated; .text may be empty.
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier string used when calling the provider API."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Lower-case provider name, e.g. ``"anthropic"`` or ``"openai"``."""
        ...
