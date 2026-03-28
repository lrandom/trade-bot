"""bot/llm/providers/openai_provider.py
---------------------------------------
OpenAI-compatible provider using ``openai.AsyncOpenAI``.

This class is also the base for GeminiProvider and DeepseekProvider, both of
which expose an OpenAI-compatible REST API and only differ in ``base_url``.
"""

import asyncio
import json

from loguru import logger
from openai import AsyncOpenAI

from bot.llm.models import LLMResponse
from bot.llm.providers.base import BaseLLMProvider
from bot.llm.tools import get_tool_for_provider

_TIMEOUT = 20.0  # seconds per API call


class OpenAIProvider(BaseLLMProvider):
    """Wraps ``openai.AsyncOpenAI`` to implement BaseLLMProvider.

    Args:
        model:    Model identifier, e.g. ``"gpt-4o"`` or ``"gpt-4o-mini"``.
        api_key:  Provider API key.
        base_url: Optional custom base URL for OpenAI-compatible endpoints.
                  Leave as ``None`` to use the official OpenAI API.
    """

    def __init__(
        self, model: str, api_key: str, base_url: str | None = None
    ) -> None:
        self._model = model
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "openai"

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    async def complete(self, system: str, user: str) -> LLMResponse:
        """Free-form chat completion for analysis steps."""
        resp = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=1024,
            ),
            timeout=_TIMEOUT,
        )
        text = resp.choices[0].message.content or ""
        logger.debug(
            "openai complete | model={} in={} out={}",
            self._model,
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
        )
        return LLMResponse(
            text=text,
            tool_data={},
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
        )

    async def complete_structured(
        self, system: str, user: str, tool_schema: dict
    ) -> LLMResponse:
        """Structured output via function_calling.

        Forces a specific function call via ``tool_choice`` so the response
        is always a JSON object matching the schema.
        """
        tool = get_tool_for_provider(tool_schema, "openai")
        resp = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=[{"type": "function", "function": tool}],
                tool_choice={
                    "type": "function",
                    "function": {"name": tool["name"]},
                },
                max_tokens=1024,
            ),
            timeout=_TIMEOUT,
        )
        tool_calls = resp.choices[0].message.tool_calls
        data: dict = {}
        if tool_calls:
            try:
                data = json.loads(tool_calls[0].function.arguments)
            except json.JSONDecodeError:
                data = {}
        logger.debug(
            "openai structured | model={} in={} out={} tool={}",
            self._model,
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
            tool_schema.get("name"),
        )
        return LLMResponse(
            text="",
            tool_data=data,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
        )
