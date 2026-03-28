"""bot/llm/tools.py
------------------
Unified tool / function-call schemas for signal generation and trade management.

The canonical format stores parameters under the "parameters" key (OpenAI style).
Use ``get_tool_for_provider()`` to adapt the schema to each provider's wire format
before passing it to the API.
"""

import copy


# ---------------------------------------------------------------------------
# Signal generation tool
# ---------------------------------------------------------------------------

SIGNAL_TOOL: dict = {
    "name": "generate_trading_signal",
    "description": (
        "Generate a structured XAUUSD trading signal based on full "
        "multi-timeframe analysis"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["BUY", "SELL", "HOLD"],
            },
            "entry_price": {
                "type": "number",
                "description": "Entry price in USD",
            },
            "stop_loss": {
                "type": "number",
                "description": "Stop loss price in USD",
            },
            "tp1": {
                "type": "number",
                "description": "Take profit 1 (33% close)",
            },
            "tp2": {
                "type": "number",
                "description": "Take profit 2 (33% close)",
            },
            "tp3": {
                "type": "number",
                "description": "Take profit 3 (34% close)",
            },
            "htf_bias": {
                "type": "string",
                "enum": ["BUY-ONLY", "SELL-ONLY", "NEUTRAL"],
            },
            "confidence": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
            },
            "reasoning": {
                "type": "string",
                "description": "Concise reasoning under 200 words",
            },
        },
        "required": [
            "action",
            "entry_price",
            "stop_loss",
            "tp1",
            "tp2",
            "tp3",
            "htf_bias",
            "confidence",
            "reasoning",
        ],
    },
}

# ---------------------------------------------------------------------------
# Trade management tool
# ---------------------------------------------------------------------------

MANAGEMENT_TOOL: dict = {
    "name": "manage_trade",
    "description": "Decision on an open XAUUSD trade",
    "parameters": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["HOLD", "EXIT", "ADJUST_SL"],
            },
            "new_stop_loss": {
                "type": "number",
                "description": "New SL if ADJUST_SL",
            },
            "reasoning": {
                "type": "string",
            },
        },
        "required": ["decision", "reasoning"],
    },
}


# ---------------------------------------------------------------------------
# Provider adapter
# ---------------------------------------------------------------------------

def get_tool_for_provider(tool: dict, provider: str) -> dict:
    """Return a deep-copied tool schema adapted for the target provider.

    - Anthropic uses ``"input_schema"`` instead of ``"parameters"``.
    - OpenAI / Gemini / Deepseek use ``"parameters"`` as-is.

    Args:
        tool:     One of SIGNAL_TOOL or MANAGEMENT_TOOL (canonical format).
        provider: Lower-case provider name, e.g. ``"anthropic"`` or ``"openai"``.

    Returns:
        A new dict suitable for passing directly to the provider's API.
    """
    t = copy.deepcopy(tool)
    if provider == "anthropic":
        t["input_schema"] = t.pop("parameters")
    return t
