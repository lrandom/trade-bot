"""bot/llm/providers/factory.py
--------------------------------
Factory functions for building LLM provider instances from bot config.

Supports:
- Default provider/model from ``settings.llm_provider`` / ``settings.llm_model``
- Per-mode overrides via ``settings.get_llm_provider_for_mode(mode)``
"""

from bot.config import settings
from bot.llm.providers.anthropic_provider import AnthropicProvider
from bot.llm.providers.base import BaseLLMProvider
from bot.llm.providers.deepseek_provider import DeepseekProvider
from bot.llm.providers.gemini_provider import GeminiProvider
from bot.llm.providers.openai_provider import OpenAIProvider


def create_provider(provider: str, model: str) -> BaseLLMProvider:
    """Instantiate a provider by name with the corresponding API key from settings.

    Args:
        provider: Lower-case provider name: ``"anthropic"``, ``"openai"``,
                  ``"gemini"``, or ``"deepseek"``.
        model:    Provider-specific model identifier string.

    Returns:
        Concrete BaseLLMProvider instance.

    Raises:
        ValueError: If ``provider`` is not one of the supported names.
    """
    match provider.lower():
        case "anthropic":
            return AnthropicProvider(model, settings.anthropic_api_key)
        case "openai":
            return OpenAIProvider(model, settings.openai_api_key)
        case "gemini":
            return GeminiProvider(model, settings.gemini_api_key)
        case "deepseek":
            return DeepseekProvider(model, settings.deepseek_api_key)
        case _:
            raise ValueError(
                f"Unknown provider: {provider!r}. "
                f"Valid providers: anthropic, openai, gemini, deepseek"
            )


def get_provider_for_manage(mode: str) -> BaseLLMProvider:
    """Provider for manage_trade() — uses LLM_PROVIDER_MANAGE/LLM_MODEL_MANAGE if set,
    falling back to the current mode's provider."""
    provider = settings.llm_provider_manage or settings.get_llm_provider_for_mode(mode)
    model = settings.llm_model_manage or settings.get_llm_model_for_mode(mode)
    return create_provider(provider, model)


def get_provider_for_mode(mode: str) -> BaseLLMProvider:
    """Return the appropriate provider for a given trading mode.

    Reads per-mode overrides from ``settings`` (e.g. ``LLM_PROVIDER_SCALP``,
    ``LLM_MODEL_SCALP``), falling back to the default ``LLM_PROVIDER`` /
    ``LLM_MODEL`` values.

    Args:
        mode: Trading mode string — ``"scalp"``, ``"intraday"``, or ``"swing"``.

    Returns:
        Concrete BaseLLMProvider instance configured for the mode.
    """
    provider = settings.get_llm_provider_for_mode(mode)
    model = settings.get_llm_model_for_mode(mode)
    return create_provider(provider, model)
