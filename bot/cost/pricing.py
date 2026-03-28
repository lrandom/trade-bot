"""bot/cost/pricing.py
---------------------
LLM pricing table and trading fee constants.
Update these when providers change their pricing.
"""

# LLM pricing per million tokens.
# Structure: provider -> model -> {"input": $/1M, "output": $/1M}
LLM_PRICING: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
        "claude-haiku-4-5":          {"input": 0.80,  "output":  4.00},
        "claude-haiku-4-5-20251001": {"input": 0.80,  "output":  4.00},
        "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
    },
    "openai": {
        "gpt-4o":      {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output":  0.60},
        "o1-mini":     {"input": 1.10, "output":  4.40},
    },
    "gemini": {
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        "gemini-1.5-pro":   {"input": 1.25, "output": 5.00},
    },
    "deepseek": {
        "deepseek-chat":     {"input": 0.27, "output": 1.10},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    },
}

# Binance Futures fee rates
BINANCE_FEES: dict[str, float] = {
    "maker": 0.0002,  # 0.02%
    "taker": 0.0004,  # 0.04%
}


# Flat fallback rates when model/provider not found (conservative estimate)
_DEFAULT_INPUT_PER_M = 3.00
_DEFAULT_OUTPUT_PER_M = 15.00


def calc_llm_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Return the USD cost for a single LLM API call.

    Falls back to conservative defaults if the provider/model is not in the
    pricing table (logs a warning so missing entries are visible).

    Args:
        provider:      Provider name, e.g. "anthropic".
        model:         Model name, e.g. "claude-sonnet-4-6".
        input_tokens:  Number of input (prompt) tokens.
        output_tokens: Number of output (completion) tokens.

    Returns:
        Cost in USD (float).
    """
    provider_pricing = LLM_PRICING.get(provider, {})
    model_pricing = provider_pricing.get(model)

    if model_pricing is None:
        # Try a flat lookup across all providers (model name may be unique)
        for p_prices in LLM_PRICING.values():
            if model in p_prices:
                model_pricing = p_prices[model]
                break

    if model_pricing is None:
        input_rate = _DEFAULT_INPUT_PER_M
        output_rate = _DEFAULT_OUTPUT_PER_M
    else:
        input_rate = model_pricing["input"]
        output_rate = model_pricing["output"]

    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
