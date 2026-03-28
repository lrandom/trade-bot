# Phase 03 — Claude LLM Engine

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: [phase-01-project-setup.md](phase-01-project-setup.md), [phase-02-data-layer.md](phase-02-data-layer.md)
- Research: [researcher-02-llm-telegram.md](research/researcher-02-llm-telegram.md)
- Blocks: phase-07 (execution), phase-08 (orchestration)

## Overview
- **Date:** 2026-03-27
- **Priority:** P0
- **Status:** pending
- Claude API integration with 4 prompt types. Structured JSON output via `tool_use`. Model selection per trading mode.

## Key Insights
- `tool_use` with `tool_choice={"type":"any"}` forces Claude to always call the tool → guaranteed JSON structure, no parsing hacks
- Token budget per request: ~10K tokens (6 TF summaries + macro + news) — well within 200K context
- Send **summaries** not raw OHLCV rows: last 5 candle values per indicator, not 200 rows
- Model mapping: scalp → `claude-haiku-4-5` (speed/cost), intraday/swing → `claude-sonnet-4-6`
- **HTF→MTF→LTF 3-step analysis**: higher TF sets directional bias, lower TF confirms entry — never trade against HTF
- **Gate logic**: if HTF bias = BUY → only BUY signals pass; if HTF = SELL → only SELL signals pass; NEUTRAL = HOLD
- **3 TF consensus required**: HTF + MTF + LTF must align before signal generated
- Chaining: macro → HTF → MTF → LTF → signal (5 sequential calls per cycle)

## Requirements

**Functional:**
- `MacroAnalysis` prompt: Fed rate + yield spread + news → macro bias BULLISH/BEARISH/NEUTRAL
- `HTFAnalysis` prompt: W1/D1/H4 → main wave position, dominant trend, key S/R, htf_bias (BUY-only / SELL-only / NEUTRAL)
- `MTFAnalysis` prompt: H4/H1 → pullback or impulse?, confirm HTF bias, potential entry zones
- `LTFAnalysis` prompt: M15/H1 → entry trigger, candle pattern confirmation, RSI/MACD LTF
- `SignalGeneration` prompt: tool_use → structured `TradingSignal` (only called if 3 TF consensus)
- `TradeManagement` prompt: running position → `hold` / `exit` / `adjust_sl`
- **Gate**: if htf_bias = BUY, signal action must be BUY (SELL blocked); vice versa
- Model selection based on mode (scalp=haiku, others=sonnet)
- Timeout handling: 15s max per API call; on timeout return `HOLD`

**Non-functional:**
- All calls async via `anthropic.AsyncAnthropic`
- Log prompt token counts per call
- Never log raw API key

## Architecture

```
bot/llm/
├── __init__.py
├── models.py              # Output dataclasses (MacroAnalysis, HTFAnalysis, ...)
├── prompts.py             # 6 prompt template builders
├── tools.py               # Unified tool/function schemas per provider format
├── engine.py              # LLMEngine — HTF→MTF→LTF gate chain
└── providers/
    ├── __init__.py
    ├── base.py            # Abstract BaseLLMProvider
    ├── anthropic_provider.py   # Claude (tool_use)
    ├── openai_provider.py      # GPT-4o / gpt-4o-mini (function_calling)
    ├── gemini_provider.py      # Gemini Flash/Pro (function_calling)
    ├── deepseek_provider.py    # Deepseek V3/R1 (JSON mode fallback)
    └── factory.py         # LLMProviderFactory — builds provider from config
```

### Provider Design Pattern

```python
# base.py — Abstract interface (ALL providers must implement)
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class LLMResponse:
    text: str             # Raw text response
    tool_data: dict       # Parsed structured output (if tool/function call used)
    input_tokens: int
    output_tokens: int

class BaseLLMProvider(ABC):
    """All LLM providers implement this interface."""

    @abstractmethod
    async def complete(self, system: str, user: str) -> LLMResponse:
        """Free-form text completion (for HTF/MTF/LTF analysis)."""
        ...

    @abstractmethod
    async def complete_structured(
        self, system: str, user: str, tool_schema: dict
    ) -> LLMResponse:
        """Structured output via tool_use / function_calling / JSON mode."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...


# factory.py — Build provider from .env config
class LLMProviderFactory:
    @staticmethod
    def create(provider: str, model: str) -> BaseLLMProvider:
        match provider.lower():
            case "anthropic": return AnthropicProvider(model)
            case "openai":    return OpenAIProvider(model)
            case "gemini":    return GeminiProvider(model)
            case "deepseek":  return DeepseekProvider(model)
            case _: raise ValueError(f"Unknown provider: {provider}")


# .env — Switch provider with 2 lines
LLM_PROVIDER=anthropic          # anthropic | openai | gemini | deepseek
LLM_MODEL=claude-sonnet-4-6     # provider-specific model name
# Per-mode override (optional):
LLM_PROVIDER_SCALP=gemini
LLM_MODEL_SCALP=gemini-2.0-flash
LLM_PROVIDER_SWING=anthropic
LLM_MODEL_SWING=claude-sonnet-4-6
```

### Provider Implementations

```python
# anthropic_provider.py
class AnthropicProvider(BaseLLMProvider):
    async def complete_structured(self, system, user, tool_schema) -> LLMResponse:
        resp = await self._client.messages.create(
            model=self.model_name,
            tools=[tool_schema],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user}],
            system=system,
        )
        tool_block = next(b for b in resp.content if b.type == "tool_use")
        return LLMResponse(text="", tool_data=tool_block.input,
                           input_tokens=resp.usage.input_tokens,
                           output_tokens=resp.usage.output_tokens)

# openai_provider.py (also used for Deepseek — same OpenAI-compatible API)
class OpenAIProvider(BaseLLMProvider):
    async def complete_structured(self, system, user, tool_schema) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self.model_name,
            tools=[{"type": "function", "function": tool_schema}],
            tool_choice={"type": "function", "function": {"name": tool_schema["name"]}},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        args = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
        return LLMResponse(text="", tool_data=args,
                           input_tokens=resp.usage.prompt_tokens,
                           output_tokens=resp.usage.completion_tokens)

# deepseek_provider.py — reuses OpenAIProvider with base_url override
class DeepseekProvider(OpenAIProvider):
    def __init__(self, model: str):
        super().__init__(model, base_url="https://api.deepseek.com")
        # Note: tool_use partially supported; fallback to JSON mode for R1
```

### Tool Schema Adapter (unified across providers)
```python
# tools.py — single schema, adapted per provider in each provider class
SIGNAL_TOOL_SCHEMA = {
    "name": "generate_trading_signal",
    "description": "Generate a structured XAUUSD trading signal",
    "parameters": {   # OpenAI format — Anthropic uses "input_schema" key
        "type": "object",
        "properties": {
            "action":       {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
            "entry_price":  {"type": "number"},
            "stop_loss":    {"type": "number"},
            "tp1":          {"type": "number"},
            "tp2":          {"type": "number"},
            "tp3":          {"type": "number"},
            "htf_bias":     {"type": "string", "enum": ["BUY-ONLY", "SELL-ONLY", "NEUTRAL"]},
            "confidence":   {"type": "integer", "minimum": 0, "maximum": 100},
            "reasoning":    {"type": "string"}
        },
        "required": ["action", "entry_price", "stop_loss", "tp1", "htf_bias", "confidence", "reasoning"]
    }
}

def get_tool_schema_for_provider(provider: str) -> dict:
    """Anthropic uses 'input_schema', OpenAI/Gemini/Deepseek use 'parameters'."""
    schema = SIGNAL_TOOL_SCHEMA.copy()
    if provider == "anthropic":
        schema["input_schema"] = schema.pop("parameters")
    return schema
```

### Data flow (HTF → MTF → LTF)
```
MarketSnapshot
    │
    ├─► build_macro_prompt()
    │       └──► Claude ──► MacroAnalysis {bias, confidence, risks}
    │
    ├─► build_htf_prompt()              [W1 / D1 / H4]
    │       └──► Claude ──► HTFAnalysis {htf_bias: BUY-only|SELL-only|NEUTRAL,
    │                                    wave_position, key_sr_levels}
    │                           │
    │                    htf_bias == NEUTRAL? ──► STOP → return HOLD
    │
    ├─► build_mtf_prompt(HTFAnalysis)   [H4 / H1]
    │       └──► Claude ──► MTFAnalysis {confirms_htf: bool,
    │                                    structure: pullback|impulse,
    │                                    entry_zone_price_range}
    │                           │
    │                    confirms_htf == False? ──► STOP → return HOLD
    │
    ├─► build_ltf_prompt(HTFAnalysis, MTFAnalysis)   [M15 / H1]
    │       └──► Claude ──► LTFAnalysis {entry_trigger: bool,
    │                                    candle_pattern, rsi, macd_signal}
    │                           │
    │                    entry_trigger == False? ──► STOP → return HOLD
    │
    └─► build_signal_prompt(Macro, HTF, MTF, LTF)   [tool_use]
            └──► Claude ──► TradingSignal {action, entry, sl, tp1/2/3,
                                           htf_bias, confidence, reasoning}

(if position open) ──► build_management_prompt() ──► ManagementDecision
```

### Gate Logic (Python)
```python
async def generate_signal(self, snapshot) -> TradingSignal:
    macro = await self.get_macro_analysis(snapshot)
    htf   = await self.get_htf_analysis(snapshot)

    # Gate 1: HTF must have clear bias
    if htf.htf_bias == "NEUTRAL":
        return TradingSignal(action="HOLD", reasoning="HTF neutral — no directional bias")

    mtf = await self.get_mtf_analysis(snapshot, htf)

    # Gate 2: MTF must confirm HTF
    if not mtf.confirms_htf:
        return TradingSignal(action="HOLD", reasoning="MTF does not confirm HTF bias")

    ltf = await self.get_ltf_analysis(snapshot, htf, mtf)

    # Gate 3: LTF must have entry trigger
    if not ltf.entry_trigger:
        return TradingSignal(action="HOLD", reasoning="No LTF entry trigger")

    # All 3 TF aligned → generate signal
    return await self._call_signal_tool(snapshot, macro, htf, mtf, ltf)
```

## Prompt Templates

### Prompt 1 — Macro Analysis
```
SYSTEM:
You are a gold market macro analyst. Analyze the provided macroeconomic data
and give a directional bias for XAUUSD. Be concise and direct.

USER:
## Macro Data ({date})
- Fed Funds Rate: {fed_rate}%
- 10Y-2Y Treasury Spread: {yield_spread}%
- Recent Headlines:
{headlines_numbered_list}

## Task
1. State overall macro BIAS: BULLISH / BEARISH / NEUTRAL for gold
2. Key macro risks (max 3 bullet points)
3. Confidence in bias: 0-100

Format your response as:
BIAS: [BULLISH|BEARISH|NEUTRAL]
CONFIDENCE: [0-100]
RISKS: [bullet points]
```

### Prompt 2 — HTF Analysis (W1 / D1 / H4)
```
SYSTEM:
You are a professional gold futures analyst specializing in higher timeframe
structure. Your job is to identify the DOMINANT WAVE and directional bias
for XAUUSD. This bias will GATE all lower timeframe entries — be decisive.

USER:
## XAUUSD Higher Timeframe Analysis
Current price: {mark_price} | Date: {date}

### Weekly (W1)
- EMA20/50/200: {w1_ema20} / {w1_ema50} / {w1_ema200}
- RSI(14): {w1_rsi} | SuperTrend: {w1_supertrend_dir}
- Last 5 candles: {w1_last5_ohlc}

### Daily (D1)
- EMA20/50/200: {d1_ema20} / {d1_ema50} / {d1_ema200}
- RSI(14): {d1_rsi} | MACD hist: {d1_macd_hist}
- SuperTrend: {d1_supertrend_dir} | ATR(14): {d1_atr}
- Key S/R detected: {d1_support_levels} / {d1_resistance_levels}

### H4
- EMA20/50/200: {h4_ema20} / {h4_ema50} / {h4_ema200}
- RSI(14): {h4_rsi} | SuperTrend: {h4_supertrend_dir}

## Task — answer each:
1. HTF_BIAS: [BUY-ONLY | SELL-ONLY | NEUTRAL]
   (BUY-ONLY = only accept long entries; SELL-ONLY = only accept shorts)
2. WAVE_POSITION: where in the big wave? (e.g. "early impulse up", "late rally near resistance", "corrective pullback")
3. KEY_SR: top 2 support + top 2 resistance levels (price numbers)
4. INVALIDATION: price that would flip the bias
5. CONFIDENCE: 0-100
```

### Prompt 3 — MTF Analysis (H4 / H1)
```
SYSTEM:
You are a gold futures analyst focused on medium-timeframe wave structure.
Given the HTF directional bias, identify whether price is in a PULLBACK
(retracement against HTF move) or IMPULSE (continuation of HTF move),
and locate the optimal entry zone.

USER:
## HTF Context
HTF Bias: {htf_bias}  |  Key S/R: {htf_key_sr}
Invalidation level: {htf_invalidation}

## H4 Timeframe
- EMA20/50: {h4_ema20} / {h4_ema50}
- RSI(14): {h4_rsi} | MACD: {h4_macd} hist: {h4_macd_hist}
- SuperTrend: {h4_supertrend_dir} | VWAP: {h4_vwap}
- Last 5 candles: {h4_last5_ohlc}

## H1 Timeframe
- EMA20/50: {h1_ema20} / {h1_ema50}
- RSI(14): {h1_rsi} | MACD hist: {h1_macd_hist}
- Last 5 candles: {h1_last5_ohlc}

## Task
1. CONFIRMS_HTF: [YES | NO] — does MTF structure support the HTF bias?
2. STRUCTURE: [PULLBACK | IMPULSE | CONSOLIDATION]
   - PULLBACK = price pulled back, potential bounce in HTF direction
   - IMPULSE = already moving in HTF direction
3. ENTRY_ZONE: price range for optimal entry [{low} - {high}]
4. REASONING: 1-2 sentences max
```

### Prompt 4 — LTF Analysis (M15 / H1)
```
SYSTEM:
You are a precision entry specialist for gold futures. Given confirmed HTF
and MTF alignment, identify whether there is a valid ENTRY TRIGGER on the
lower timeframe. Be strict — only confirm if the trigger is clear.

USER:
## Context
HTF Bias: {htf_bias}  |  MTF Structure: {mtf_structure}
Entry Zone: {mtf_entry_zone}  |  Current price: {mark_price}

## M15 Timeframe
- EMA20/50: {m15_ema20} / {m15_ema50}
- RSI(14): {m15_rsi} | MACD: {m15_macd} hist: {m15_macd_hist}
- Last 5 candles (OHLC + pattern): {m15_last5_ohlc}
- Volume vs avg: {m15_volume_ratio}

## H1 Timeframe (confirmation)
- RSI(14): {h1_rsi} | EMA20: {h1_ema20}
- SuperTrend: {h1_supertrend_dir}

## Task
1. ENTRY_TRIGGER: [YES | NO]
   YES only if: price in entry zone + candle pattern confirms + RSI not overbought/oversold against HTF
2. CANDLE_PATTERN: pattern observed (e.g. "bullish engulfing", "pin bar", "none")
3. ENTRY_PRICE: specific entry price suggestion
4. REASONING: 1-2 sentences max
```

### Prompt 5 — Signal Generation (tool_use)
```
SYSTEM:
You are a gold futures signal generator. All three timeframes (HTF/MTF/LTF)
have already confirmed alignment. Your job is to produce precise entry parameters.
The direction is LOCKED by HTF bias — do not contradict it.

USER:
## Confirmed Analysis Summary
Trading Mode: {mode}  |  Current Price: {mark_price}
ATR(14) on {primary_tf}: {atr}

Macro: {macro_bias} ({macro_confidence}%) — {macro_risks_1line}
HTF ({htf_tfs}): {htf_bias} — wave: {htf_wave_position}
MTF ({mtf_tfs}): {mtf_structure} — entry zone: {mtf_entry_zone}
LTF ({ltf_tfs}): trigger={ltf_entry_trigger} — pattern: {ltf_candle_pattern}
Key S/R: support {htf_support} | resistance {htf_resistance}

## Task
Generate trading signal via generate_trading_signal tool.
- action: MUST match HTF bias ({htf_bias})
- entry: {ltf_entry_price} (LTF confirmed level)
- stop_loss: below/above key S/R or {sl_multiplier}x ATR = {sl_distance}
- tp1: first resistance/support (33% close)
- tp2: second resistance/support (33% close)
- tp3: HTF target or {tp3_multiplier}x ATR from entry (34% close)
- confidence: weight macro + HTF + MTF + LTF alignment (0-100)
```

### Tool Schema (Signal Generation)
```python
SIGNAL_TOOL = {
    "name": "generate_trading_signal",
    "description": "Generate a structured trading signal for XAUUSD futures",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["BUY", "SELL", "HOLD"],
                "description": "Trade direction or no trade"
            },
            "entry_price": {
                "type": "number",
                "description": "Suggested entry price in USD"
            },
            "stop_loss": {
                "type": "number",
                "description": "Stop loss price in USD"
            },
            "tp1": {"type": "number", "description": "Take profit 1 (33% close)"},
            "tp2": {"type": "number", "description": "Take profit 2 (33% close)"},
            "tp3": {"type": "number", "description": "Take profit 3 (34% close)"},
            "confidence": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Signal confidence 0-100"
            },
            "trend_bias": {
                "type": "string",
                "enum": ["BULLISH", "BEARISH", "NEUTRAL"]
            },
            "reasoning": {
                "type": "string",
                "description": "Concise reasoning under 200 words"
            }
        },
        "required": ["action", "entry_price", "stop_loss", "tp1", "confidence", "trend_bias", "reasoning"]
    }
}
```

### Prompt 6 — Trade Management
```
SYSTEM:
You are a gold futures trade manager. A position is currently open. Evaluate
whether to hold, exit, or adjust the stop loss based on current market conditions.
Capital preservation is the primary objective.

USER:
## Open Position
Side: {side}  Entry: {entry}  Current P&L: {pnl_pct}%
SL: {stop_loss}  TP1: {tp1} ({tp1_hit})  TP2: {tp2} ({tp2_hit})  TP3: {tp3} ({tp3_hit})
Time in trade: {duration}

## Current Market
Mark Price: {mark_price}
ATR(14): {atr}
SuperTrend direction: {supertrend_dir}
RSI(14): {rsi}

## Macro (latest)
{macro_summary}

## Decision
Use the manage_trade tool to give your decision.
```

### Trade Management Tool Schema
```python
MANAGEMENT_TOOL = {
    "name": "manage_trade",
    "description": "Decision on open trade",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["HOLD", "EXIT", "ADJUST_SL"],
                "description": "What to do with the open position"
            },
            "new_stop_loss": {
                "type": "number",
                "description": "New SL price if decision is ADJUST_SL"
            },
            "reasoning": {"type": "string"}
        },
        "required": ["decision", "reasoning"]
    }
}
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/llm/client.py` | AsyncAnthropic singleton |
| create | `bot/llm/models.py` | HTFAnalysis, MTFAnalysis, LTFAnalysis, TradingSignal dataclasses |
| create | `bot/llm/tools.py` | SIGNAL_TOOL + MANAGEMENT_TOOL schemas |
| create | `bot/llm/prompts.py` | 6 prompt builder functions |
| create | `bot/llm/engine.py` | LLMEngine with HTF→MTF→LTF gate chain |

## Implementation Steps

0. **providers/base.py** — `BaseLLMProvider` abstract class with `complete()` + `complete_structured()` + `model_name`

0b. **providers/factory.py** — `LLMProviderFactory.create(provider, model)` reads from settings; supports per-mode override via `LLM_PROVIDER_SCALP`, `LLM_PROVIDER_SWING`, etc.

0c. **providers/anthropic_provider.py** — implements `BaseLLMProvider` using `anthropic.AsyncAnthropic`, maps `input_schema` key

0d. **providers/openai_provider.py** — implements using `openai.AsyncOpenAI`, maps `parameters` key, parses `tool_calls[0].function.arguments`

0e. **providers/gemini_provider.py** — implements using `google-generativeai` or OpenAI-compat endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`)

0f. **providers/deepseek_provider.py** — subclass of `OpenAIProvider` with `base_url="https://api.deepseek.com"`; falls back to JSON mode for R1

0g. **tools.py** — `SIGNAL_TOOL_SCHEMA` + `MANAGEMENT_TOOL_SCHEMA` in neutral format + `get_tool_schema_for_provider(provider)` adapter

1. **models.py** — dataclasses:
   ```python
   @dataclass
   class MacroAnalysis:
       bias: str          # BULLISH | BEARISH | NEUTRAL
       confidence: int
       risks: str

   @dataclass
   class HTFAnalysis:
       htf_bias: str      # BUY-ONLY | SELL-ONLY | NEUTRAL
       wave_position: str
       key_support: list[float]
       key_resistance: list[float]
       invalidation: float
       confidence: int

   @dataclass
   class MTFAnalysis:
       confirms_htf: bool
       structure: str     # PULLBACK | IMPULSE | CONSOLIDATION
       entry_zone: tuple[float, float]
       reasoning: str

   @dataclass
   class LTFAnalysis:
       entry_trigger: bool
       candle_pattern: str
       entry_price: float
       reasoning: str

   @dataclass
   class TradingSignal:
       action: str        # BUY | SELL | HOLD
       entry_price: float
       stop_loss: float
       tp1: float; tp2: float; tp3: float
       htf_bias: str
       confidence: int
       reasoning: str
   ```

2. **prompts.py** — 6 builder functions:
   - `build_macro_prompt(snapshot) -> str`
   - `build_htf_prompt(snapshot) -> str`
   - `build_mtf_prompt(snapshot, htf: HTFAnalysis) -> str`
   - `build_ltf_prompt(snapshot, htf, mtf) -> str`
   - `build_signal_prompt(snapshot, macro, htf, mtf, ltf) -> str`
   - `build_management_prompt(position, snapshot) -> str`

3. **engine.py** — `LLMEngine` with gate chain (see Gate Logic section above)

4. **Signal extraction** for tool_use:
   ```python
   response = await client.messages.create(
       model=model, max_tokens=1024,
       tools=[SIGNAL_TOOL], tool_choice={"type": "any"},
       messages=[{"role": "user", "content": prompt}]
   )
   tool_block = next(b for b in response.content if b.type == "tool_use")
   return TradingSignal(**tool_block.input)
   ```

5. **HTF/MTF/LTF text parsing** (non-tool_use): use regex to extract labeled fields (CONFIRMS_HTF: YES/NO, HTF_BIAS: BUY-ONLY, etc.)

6. **Timeout**: `asyncio.wait_for(call, timeout=15.0)` on all 5 calls; on error → return `TradingSignal(action='HOLD')`

7. **Token logging**: log `response.usage` after each call

## Todo

- [ ] `providers/base.py` — `BaseLLMProvider` abstract interface
- [ ] `providers/anthropic_provider.py`
- [ ] `providers/openai_provider.py`
- [ ] `providers/gemini_provider.py`
- [ ] `providers/deepseek_provider.py` (subclass OpenAI)
- [ ] `providers/factory.py` — factory + per-mode provider selection
- [ ] `tools.py` — unified schema + `get_tool_schema_for_provider()` adapter
- [ ] `models.py` — 5 dataclasses (Macro/HTF/MTF/LTF/Signal)
- [ ] `prompts.py` — 6 prompt builders
- [ ] `engine.py` — LLMEngine with HTF→MTF→LTF gate chain
- [ ] Text parser for HTF/MTF/LTF text responses (regex)
- [ ] Timeout wrapper (15s) on all API calls
- [ ] Fallback HOLD on error/timeout
- [ ] Token usage logging per provider

## Success Criteria
- `engine.generate_signal(snapshot)` returns `TradingSignal` with all required fields
- `action='HOLD'` returned when API times out
- tool_use schema enforces JSON structure (no `json.loads` guesswork)
- Token counts logged per call

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude API timeout during fast market | High | 15s asyncio.wait_for, fallback HOLD |
| Tool_use returns wrong field names | Medium | Pin anthropic SDK version, test schema |
| Hallucinated entry/SL prices far from market | High | Risk engine validates price sanity (phase-04) |
| Cost overrun on sonnet-4-6 | Medium | Haiku for scalp; token logging for budget tracking |

## Security Considerations
- API key from `Settings` only, never in prompt strings
- Do not log full prompt text at INFO level (contains market data, verbose) — use DEBUG only
- Reasoning field from Claude truncated to 500 chars in DB to prevent prompt injection storage

## Next Steps
- Phase 04: `TradingSignal.entry/sl` validated against ATR limits before order placed
- Phase 07: `TradingSignal` consumed by execution engine
