# Phase C — LLM Engine

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-03-claude-llm-engine.md`
- Depends on: [phase-A-foundation.md](phase-A-foundation.md), [phase-B-data-layer.md](phase-B-data-layer.md)
- Blocks: Phase E (Execution), Phase G (Orchestration)

## Overview
- **Date:** 2026-03-28
- **Priority:** P0
- **Status:** pending
- Provider pattern (Anthropic/OpenAI/Gemini/Deepseek), HTF→MTF→LTF 3-gate analysis chain, 6 prompt builders, structured JSON output via tool_use/function_calling.

## Key Insights
- `tool_use` with `tool_choice={"type":"any"}` forces Claude to always call the tool — guaranteed JSON, no parsing guesswork
- Send indicator summaries (last 5 values), not raw 200-row OHLCV — ~9K tokens/cycle max
- Gate logic saves tokens: HTF NEUTRAL → stop after 2 calls; average ~6K/1.2K with filtering
- Model mapping: scalp → haiku (speed/cost), intraday/swing → sonnet
- Per-mode provider override via env: `LLM_PROVIDER_SCALP`, `LLM_PROVIDER_SWING`, etc.
- Deepseek uses OpenAI-compatible API (same provider impl, different `base_url`)
- LLM hallucination on price levels: 5-10% — always validate SL/TP via Risk Engine (Phase D)
- All calls `asyncio.wait_for(..., timeout=15.0)` — on timeout return HOLD signal

## Requirements

**Functional:**
- 4 provider implementations: Anthropic, OpenAI, Gemini, Deepseek
- Provider factory reads from `Settings` with per-mode override
- 6 prompt builders: macro, HTF, MTF, LTF, signal (tool_use), management (tool_use)
- HTF→MTF→LTF gate chain: 3 sequential gates, early exit on HOLD
- `generate_signal(snapshot) -> TradingSignal`
- `manage_trade(position, snapshot) -> ManagementDecision`
- 15s timeout on all API calls; fallback HOLD on timeout/error

**Non-functional:**
- Log token counts per call (for Phase I cost tracking)
- Never log full prompt text at INFO (use DEBUG)
- Reasoning truncated to 500 chars before DB storage

## Architecture

```
bot/llm/
├── __init__.py
├── models.py              # Output dataclasses
├── prompts.py             # 6 prompt builder functions
├── tools.py               # SIGNAL_TOOL + MANAGEMENT_TOOL schemas + adapter
├── engine.py              # LLMEngine — HTF→MTF→LTF gate chain
└── providers/
    ├── __init__.py
    ├── base.py            # Abstract BaseLLMProvider
    ├── anthropic_provider.py
    ├── openai_provider.py
    ├── gemini_provider.py
    ├── deepseek_provider.py  # subclass of OpenAIProvider with base_url override
    └── factory.py         # LLMProviderFactory
```

### Data flow
```
MarketSnapshot
  │
  ├─► build_macro_prompt()   → provider.complete()  → MacroAnalysis
  ├─► build_htf_prompt()     → provider.complete()  → HTFAnalysis
  │       htf_bias == NEUTRAL? → STOP → return HOLD
  ├─► build_mtf_prompt(htf)  → provider.complete()  → MTFAnalysis
  │       confirms_htf == False? → STOP → return HOLD
  ├─► build_ltf_prompt(htf,mtf) → provider.complete() → LTFAnalysis
  │       entry_trigger == False? → STOP → return HOLD
  └─► build_signal_prompt(all) → provider.complete_structured(SIGNAL_TOOL) → TradingSignal
```

### Provider interface
```python
@dataclass
class LLMResponse:
    text: str
    tool_data: dict
    input_tokens: int
    output_tokens: int

class BaseLLMProvider(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str) -> LLMResponse: ...

    @abstractmethod
    async def complete_structured(self, system: str, user: str, tool_schema: dict) -> LLMResponse: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/llm/providers/base.py` | `LLMResponse` + `BaseLLMProvider` ABC |
| create | `bot/llm/providers/anthropic_provider.py` | Claude via tool_use |
| create | `bot/llm/providers/openai_provider.py` | GPT via function_calling |
| create | `bot/llm/providers/gemini_provider.py` | Gemini via OpenAI-compat endpoint |
| create | `bot/llm/providers/deepseek_provider.py` | Deepseek (subclass of OpenAI) |
| create | `bot/llm/providers/factory.py` | `LLMProviderFactory.create()` |
| create | `bot/llm/tools.py` | Tool schemas + provider adapter |
| create | `bot/llm/models.py` | 5 output dataclasses |
| create | `bot/llm/prompts.py` | 6 prompt builder functions |
| create | `bot/llm/engine.py` | LLMEngine gate chain |

## Implementation Steps

1. **`providers/base.py`**:
   - `@dataclass class LLMResponse` with `text`, `tool_data`, `input_tokens`, `output_tokens`
   - `class BaseLLMProvider(ABC)` with abstract `complete()`, `complete_structured()`, `model_name`

2. **`providers/anthropic_provider.py`** — `AnthropicProvider(BaseLLMProvider)`:
   - Init: `self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)`
   - `complete()`: `messages.create(model, messages, system, max_tokens=1024)` → `LLMResponse(text=resp.content[0].text, tool_data={}, ...)`
   - `complete_structured()`: add `tools=[tool_schema_adapted]`, `tool_choice={"type":"any"}` → extract `tool_block.input` from content
   - Schema adapter: rename `parameters` key to `input_schema` for Anthropic

3. **`providers/openai_provider.py`** — `OpenAIProvider(BaseLLMProvider)`:
   - Init: `self._client = openai.AsyncOpenAI(api_key=..., base_url=base_url)`
   - `complete()`: `chat.completions.create(messages=[system+user])` → text from `choices[0].message.content`
   - `complete_structured()`: use `tools=[{"type":"function","function":schema}]`, `tool_choice={"type":"function","function":{"name":schema["name"]}}` → parse `tool_calls[0].function.arguments` with `json.loads`

4. **`providers/gemini_provider.py`** — `GeminiProvider(OpenAIProvider)`:
   - Subclass with `base_url="https://generativelanguage.googleapis.com/v1beta/openai/"`, `api_key=settings.GEMINI_API_KEY`
   - Same OpenAI-compatible function_calling interface

5. **`providers/deepseek_provider.py`** — `DeepseekProvider(OpenAIProvider)`:
   - Subclass with `base_url="https://api.deepseek.com"`, `api_key=settings.DEEPSEEK_API_KEY`
   - Note in docstring: R1 model may not support tool_use — fallback to JSON mode if needed

6. **`providers/factory.py`** — `LLMProviderFactory`:
   ```python
   @staticmethod
   def create(provider: str, model: str) -> BaseLLMProvider:
       match provider.lower():
           case "anthropic": return AnthropicProvider(model)
           case "openai":    return OpenAIProvider(model)
           case "gemini":    return GeminiProvider(model)
           case "deepseek":  return DeepseekProvider(model)
           case _: raise ValueError(f"Unknown LLM provider: {provider}")

   @staticmethod
   def for_mode(mode: str) -> BaseLLMProvider:
       """Reads per-mode override from settings, falls back to default."""
       provider = getattr(settings, f"LLM_PROVIDER_{mode.upper()}", settings.LLM_PROVIDER)
       model    = getattr(settings, f"LLM_MODEL_{mode.upper()}", settings.LLM_MODEL)
       return LLMProviderFactory.create(provider, model)
   ```

7. **`tools.py`** — define two tool schemas in neutral format:
   - `SIGNAL_TOOL_SCHEMA`: `name`, `description`, `parameters` (OpenAI-style) with `action`, `entry_price`, `stop_loss`, `tp1`, `tp2`, `tp3`, `confidence`, `trend_bias`, `reasoning`
   - `MANAGEMENT_TOOL_SCHEMA`: `decision` (HOLD/EXIT/ADJUST_SL), `new_stop_loss`, `reasoning`
   - `def get_tool_schema_for_provider(schema: dict, provider: str) -> dict`: if Anthropic, rename `parameters` → `input_schema`

8. **`models.py`** — 5 dataclasses:
   ```python
   @dataclass
   class MacroAnalysis:
       bias: str        # BULLISH | BEARISH | NEUTRAL
       confidence: int
       risks: str

   @dataclass
   class HTFAnalysis:
       htf_bias: str    # BUY-ONLY | SELL-ONLY | NEUTRAL
       wave_position: str
       key_support: list[float]
       key_resistance: list[float]
       invalidation: float
       confidence: int

   @dataclass
   class MTFAnalysis:
       confirms_htf: bool
       structure: str   # PULLBACK | IMPULSE | CONSOLIDATION
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
       action: str          # BUY | SELL | HOLD
       entry_price: float
       stop_loss: float
       tp1: float
       tp2: float
       tp3: float
       htf_bias: str
       confidence: int
       reasoning: str
       atr: float = 0.0     # injected from snapshot for risk validation

   @dataclass
   class ManagementDecision:
       decision: str        # HOLD | EXIT | ADJUST_SL
       new_stop_loss: float | None
       reasoning: str
   ```

9. **`prompts.py`** — 6 builder functions (see design spec for exact templates):
   - `build_macro_prompt(snapshot) -> tuple[str, str]` — returns `(system, user)`
   - `build_htf_prompt(snapshot) -> tuple[str, str]`
   - `build_mtf_prompt(snapshot, htf: HTFAnalysis) -> tuple[str, str]`
   - `build_ltf_prompt(snapshot, htf, mtf) -> tuple[str, str]`
   - `build_signal_prompt(snapshot, macro, htf, mtf, ltf) -> tuple[str, str]`
   - `build_management_prompt(position, snapshot) -> tuple[str, str]`
   - Each function formats indicator summary: last 5 values of key columns, not full df
   - Include correlation + cycle context from Phase K (use empty values if Phase K not yet implemented)

10. **`engine.py`** — `LLMEngine`:
    ```python
    class LLMEngine:
        def __init__(self, mode: str):
            self.provider = LLMProviderFactory.for_mode(mode)

        async def generate_signal(self, snapshot: MarketSnapshot) -> TradingSignal:
            try:
                macro = await self._call_macro(snapshot)
                htf   = await self._call_htf(snapshot)

                if htf.htf_bias == "NEUTRAL":
                    return TradingSignal(action="HOLD", reasoning="HTF neutral")

                mtf = await self._call_mtf(snapshot, htf)
                if not mtf.confirms_htf:
                    return TradingSignal(action="HOLD", reasoning="MTF does not confirm HTF")

                ltf = await self._call_ltf(snapshot, htf, mtf)
                if not ltf.entry_trigger:
                    return TradingSignal(action="HOLD", reasoning="No LTF entry trigger")

                signal = await self._call_signal(snapshot, macro, htf, mtf, ltf)
                # Inject ATR for risk validation
                signal.atr = snapshot.indicators.get(snapshot.mode_primary_tf, {}).get('atr_14', 0.0)
                return signal
            except Exception as e:
                logger.exception(f"LLM engine error: {e}")
                return TradingSignal(action="HOLD", reasoning=f"Engine error: {str(e)[:100]}")

        async def _call_with_timeout(self, coro):
            return await asyncio.wait_for(coro, timeout=15.0)
    ```

11. **Text response parsers** (for HTF/MTF/LTF free-text responses):
    - `parse_htf_response(text) -> HTFAnalysis`: regex-extract `HTF_BIAS: BUY-ONLY`, `WAVE_POSITION: ...`, `CONFIDENCE: \d+`, etc.
    - `parse_mtf_response(text) -> MTFAnalysis`: extract `CONFIRMS_HTF: YES|NO`, `STRUCTURE: ...`
    - `parse_ltf_response(text) -> LTFAnalysis`: extract `ENTRY_TRIGGER: YES|NO`, `CANDLE_PATTERN: ...`, `ENTRY_PRICE: \d+`
    - On parse failure: return conservative default (NEUTRAL / confirms_htf=False / entry_trigger=False)

12. **Token logging**: after every provider call, `logger.debug(f"[{call_type}] tokens: in={resp.input_tokens} out={resp.output_tokens}")`

## Todo

- [ ] `providers/base.py` — `LLMResponse` + `BaseLLMProvider`
- [ ] `providers/anthropic_provider.py` — tool_use
- [ ] `providers/openai_provider.py` — function_calling
- [ ] `providers/gemini_provider.py` — OpenAI compat endpoint
- [ ] `providers/deepseek_provider.py` — OpenAI subclass
- [ ] `providers/factory.py` — factory + per-mode selector
- [ ] `tools.py` — 2 schemas + provider adapter
- [ ] `models.py` — 6 dataclasses (incl ManagementDecision)
- [ ] `prompts.py` — 6 prompt builders returning (system, user)
- [ ] `engine.py` — LLMEngine gate chain
- [ ] Text parsers for HTF/MTF/LTF responses
- [ ] 15s timeout wrapper on all calls
- [ ] Token logging per call
- [ ] Fallback HOLD on timeout/parse error

## Success Criteria
- `engine.generate_signal(snapshot)` returns `TradingSignal` with all fields
- `action='HOLD'` returned when any gate fails
- `action='HOLD'` returned on 15s timeout
- `tool_use` enforces JSON structure — no raw text parsing for signal
- Token counts logged at DEBUG level for each call

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude API timeout during fast market | High | 15s `asyncio.wait_for`, fallback HOLD |
| Tool schema mismatch between providers | Medium | Adapter function in `tools.py`; test each provider |
| Hallucinated SL/TP far from market | High | Phase D risk engine validates price sanity against ATR |
| Deepseek R1 no tool_use support | Medium | Fall back to JSON mode via system prompt; parse manually |

## Security Considerations
- API keys from `Settings` only — never in prompt strings
- Do not log full prompt at INFO — DEBUG only
- `reasoning` field truncated to 500 chars before DB write

## Next Steps
- Phase D: `TradingSignal.entry_price/stop_loss` validated against ATR bounds
- Phase E: `TradingSignal` consumed by execution engine
- Phase I: token counts passed to `CostTracker.log_llm_call()`
