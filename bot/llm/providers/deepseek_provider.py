"""bot/llm/providers/deepseek_provider.py
-----------------------------------------
Deepseek provider via the OpenAI-compatible REST endpoint.

Deepseek V3 and R1 expose an OpenAI-compatible API at:
    https://api.deepseek.com

This lets us reuse OpenAIProvider with only the base URL and provider name
overridden.

Note: Deepseek R1 has limited tool_use support; if structured calls fail
the engine's exception handler will return a HOLD signal automatically.
"""

from bot.llm.providers.openai_provider import OpenAIProvider

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepseekProvider(OpenAIProvider):
    """Deepseek provider using the OpenAI-compatible endpoint.

    Args:
        model:   Deepseek model identifier, e.g. ``"deepseek-chat"`` or
                 ``"deepseek-reasoner"``.
        api_key: Deepseek API key.
    """

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=_DEEPSEEK_BASE_URL,
        )

    @property
    def provider_name(self) -> str:
        return "deepseek"
