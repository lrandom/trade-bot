"""bot/llm/providers/gemini_provider.py
---------------------------------------
Google Gemini provider via the OpenAI-compatible REST endpoint.

Gemini exposes an OpenAI-compatible API at:
    https://generativelanguage.googleapis.com/v1beta/openai/

This lets us reuse OpenAIProvider entirely, with only the base URL and
provider name overridden.
"""

from bot.llm.providers.openai_provider import OpenAIProvider

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GeminiProvider(OpenAIProvider):
    """Google Gemini provider using the OpenAI-compatible endpoint.

    Args:
        model:   Gemini model identifier, e.g. ``"gemini-2.0-flash"``.
        api_key: Google Gemini API key.
    """

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=_GEMINI_BASE_URL,
        )

    @property
    def provider_name(self) -> str:
        return "gemini"
