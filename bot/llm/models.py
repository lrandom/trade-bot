"""bot/llm/models.py
------------------
Dataclasses for all LLM outputs produced by the LLM engine.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMResponse:
    """Raw response from any LLM provider."""
    text: str
    tool_data: dict
    input_tokens: int
    output_tokens: int


@dataclass
class MacroAnalysis:
    """Macro-economic directional bias for XAUUSD."""
    bias: str          # BULLISH | BEARISH | NEUTRAL
    confidence: int    # 0-100
    risks: str         # pipe-separated bullet points
    raw: str = ""


@dataclass
class HTFAnalysis:
    """Higher timeframe structural analysis (W1 / D1 / H4)."""
    htf_bias: str           # BUY-ONLY | SELL-ONLY | NEUTRAL
    wave_position: str
    key_support: list       # list[float]
    key_resistance: list    # list[float]
    invalidation: float
    confidence: int         # 0-100
    raw: str = ""


@dataclass
class MTFAnalysis:
    """Medium timeframe confirmation (H4 / H1)."""
    confirms_htf: bool
    structure: str          # PULLBACK | IMPULSE | CONSOLIDATION
    entry_zone_low: float
    entry_zone_high: float
    reasoning: str
    raw: str = ""


@dataclass
class LTFAnalysis:
    """Lower timeframe entry trigger (M15 / H1)."""
    entry_trigger: bool
    candle_pattern: str
    entry_price: float
    reasoning: str
    raw: str = ""


@dataclass
class TradingSignal:
    """Final structured trading signal output."""
    action: str             # BUY | SELL | HOLD
    entry_price: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    htf_bias: str = "NEUTRAL"
    confidence: int = 0
    reasoning: str = ""
    mode: str = ""
    provider: str = ""
    model: str = ""

    @classmethod
    def hold(cls, reason: str = "No signal") -> "TradingSignal":
        """Convenience constructor for a HOLD signal."""
        return cls(action="HOLD", reasoning=reason)


@dataclass
class ManagementDecision:
    """Decision on an existing open position."""
    decision: str                       # HOLD | EXIT | ADJUST_SL
    new_stop_loss: Optional[float] = None
    reasoning: str = ""
