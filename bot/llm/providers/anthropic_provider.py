"""bot/llm/providers/anthropic_provider.py
------------------------------------------
Anthropic Claude provider using the official ``anthropic`` SDK.

Key differences from OpenAI:
- System prompt is a top-level parameter, not a message role.
- Structured output uses ``tool_use`` blocks with ``tool_choice={"type":"any"}``.
- Tool schema key is ``"input_schema"`` (not ``"parameters"``).
- Token usage lives in ``resp.usage.input_tokens`` / ``resp.usage.output_tokens``.
"""

import asyncio

import anthropic
from loguru import logger

from bot.llm.models import LLMResponse
from bot.llm.providers.base import BaseLLMProvider
from bot.llm.tools import get_tool_for_provider

_TIMEOUT = 20.0  # seconds per API call


class AnthropicProvider(BaseLLMProvider):
    """Wraps ``anthropic.AsyncAnthropic`` to implement BaseLLMProvider."""

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    async def complete(self, system: str, user: str) -> LLMResponse:
        """Free-form text completion for analysis steps."""
        resp = await asyncio.wait_for(
            self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=_TIMEOUT,
        )
        text = resp.content[0].text if resp.content else ""
        logger.debug(
            "anthropic complete | model={} in={} out={}",
            self._model,
            resp.usage.input_tokens,
            resp.usage.output_tokens,
        )
        return LLMResponse(
            text=text,
            tool_data={},
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )

    async def complete_structured(
        self, system: str, user: str, tool_schema: dict
    ) -> LLMResponse:
        """Structured output via tool_use.

        ``tool_choice={"type": "any"}`` forces Claude to always call a tool,
        guaranteeing a JSON structure without parsing hacks.
        """
        tool = get_tool_for_provider(tool_schema, "anthropic")
        resp = await asyncio.wait_for(
            self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                tools=[tool],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user}],
            ),
            timeout=_TIMEOUT,
        )
        tool_block = next(
            (b for b in resp.content if b.type == "tool_use"), None
        )
        data = tool_block.input if tool_block else {}
        # Claude may also emit a text block alongside the tool call
        text = next(
            (b.text for b in resp.content if hasattr(b, "text")), ""
        )
        logger.debug(
            "anthropic structured | model={} in={} out={} tool={}",
            self._model,
            resp.usage.input_tokens,
            resp.usage.output_tokens,
            tool_schema.get("name"),
        )
        return LLMResponse(
            text=text,
            tool_data=data,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
