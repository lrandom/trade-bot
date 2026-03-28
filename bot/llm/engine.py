"""bot/llm/engine.py
--------------------
LLMEngine — the central orchestrator for the HTF→MTF→LTF gate chain.

Flow per trading cycle:
    1. Macro analysis           (text completion, regex-parsed)
    2. HTF analysis             (text completion, regex-parsed)
       Gate: htf_bias != NEUTRAL — else return HOLD
    3. MTF analysis             (text completion, regex-parsed)
       Gate: confirms_htf == True — else return HOLD
    4. LTF analysis             (text completion, regex-parsed)
       Gate: entry_trigger == True — else return HOLD
    5. Signal generation        (tool_use / function_calling, JSON)
       All 3 TF aligned — generates TradingSignal

For open positions:
    manage_trade()              (tool_use, JSON) → ManagementDecision
"""

import re

from loguru import logger

from bot.data.snapshot import MarketSnapshot
from bot.llm.models import (
    HTFAnalysis,
    LTFAnalysis,
    MTFAnalysis,
    MacroAnalysis,
    ManagementDecision,
    TradingSignal,
)
from bot.llm.prompts import (
    build_htf_prompt,
    build_ltf_prompt,
    build_macro_prompt,
    build_management_prompt,
    build_mtf_prompt,
    build_signal_prompt,
)
from bot.llm.providers.base import BaseLLMProvider
from bot.llm.providers.factory import get_provider_for_mode
from bot.llm.tools import MANAGEMENT_TOOL, SIGNAL_TOOL


class LLMEngine:
    """Orchestrates multi-step LLM analysis for XAUUSD trading signals.

    Args:
        mode: Trading mode string — ``"scalp"``, ``"intraday"``, or ``"swing"``.
              Determines which provider/model is used via factory.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.provider: BaseLLMProvider = get_provider_for_mode(mode)

    # ------------------------------------------------------------------
    # Public analysis methods (each independently guarded)
    # ------------------------------------------------------------------

    async def get_macro_analysis(self, snapshot: MarketSnapshot) -> MacroAnalysis:
        """Step 1: Macro-economic bias analysis.

        Returns a neutral MacroAnalysis on any API error.
        """
        system, user = build_macro_prompt(snapshot)
        try:
            resp = await self.provider.complete(system, user)
            logger.debug(
                "macro | in={} out={}",
                resp.input_tokens,
                resp.output_tokens,
            )
            return self._parse_macro(resp.text)
        except Exception as e:
            logger.warning("Macro analysis failed: {}", e)
            return MacroAnalysis(bias="NEUTRAL", confidence=50, risks="API error")

    async def get_htf_analysis(self, snapshot: MarketSnapshot) -> HTFAnalysis:
        """Step 2: Higher timeframe structural analysis (W1/D1/H4).

        Returns a neutral HTFAnalysis with zero confidence on any API error.
        """
        system, user = build_htf_prompt(snapshot)
        try:
            resp = await self.provider.complete(system, user)
            logger.debug(
                "HTF | in={} out={}",
                resp.input_tokens,
                resp.output_tokens,
            )
            return self._parse_htf(resp.text)
        except Exception as e:
            logger.warning("HTF analysis failed: {}", e)
            return HTFAnalysis(
                htf_bias="NEUTRAL",
                wave_position="unknown",
                key_support=[],
                key_resistance=[],
                invalidation=0.0,
                confidence=0,
            )

    async def get_mtf_analysis(
        self, snapshot: MarketSnapshot, htf: HTFAnalysis
    ) -> MTFAnalysis:
        """Step 3: Medium timeframe confirmation (H4/H1).

        Returns a non-confirming MTFAnalysis on any API error so the gate
        blocks signal generation.
        """
        system, user = build_mtf_prompt(snapshot, htf)
        try:
            resp = await self.provider.complete(system, user)
            logger.debug(
                "MTF | in={} out={}",
                resp.input_tokens,
                resp.output_tokens,
            )
            return self._parse_mtf(resp.text)
        except Exception as e:
            logger.warning("MTF analysis failed: {}", e)
            return MTFAnalysis(
                confirms_htf=False,
                structure="CONSOLIDATION",
                entry_zone_low=0.0,
                entry_zone_high=0.0,
                reasoning="API error",
            )

    async def get_ltf_analysis(
        self,
        snapshot: MarketSnapshot,
        htf: HTFAnalysis,
        mtf: MTFAnalysis,
    ) -> LTFAnalysis:
        """Step 4: Lower timeframe entry trigger (M15/H1).

        Returns a non-triggered LTFAnalysis on any API error so the gate
        blocks signal generation.
        """
        system, user = build_ltf_prompt(snapshot, htf, mtf)
        try:
            resp = await self.provider.complete(system, user)
            logger.debug(
                "LTF | in={} out={}",
                resp.input_tokens,
                resp.output_tokens,
            )
            return self._parse_ltf(resp.text)
        except Exception as e:
            logger.warning("LTF analysis failed: {}", e)
            return LTFAnalysis(
                entry_trigger=False,
                candle_pattern="none",
                entry_price=0.0,
                reasoning="API error",
            )

    # ------------------------------------------------------------------
    # Main signal generation
    # ------------------------------------------------------------------

    async def generate_signal(self, snapshot: MarketSnapshot) -> TradingSignal:
        """Run the full HTF→MTF→LTF gate chain and generate a trading signal.

        Returns ``TradingSignal.hold()`` at any gate failure or API error.

        Args:
            snapshot: Fully populated MarketSnapshot from the data layer.

        Returns:
            TradingSignal with action=BUY|SELL (all gates passed) or HOLD.
        """
        # Step 1 — Macro (informational; does not gate)
        macro = await self.get_macro_analysis(snapshot)

        # Step 2 — HTF
        htf = await self.get_htf_analysis(snapshot)

        # Gate 1: HTF must have a clear directional bias
        if htf.htf_bias == "NEUTRAL":
            logger.info(
                "HOLD — HTF neutral (conf={}%)", htf.confidence
            )
            return TradingSignal.hold("HTF neutral — no directional bias")

        # Step 3 — MTF
        mtf = await self.get_mtf_analysis(snapshot, htf)

        # Gate 2: MTF must confirm the HTF bias
        if not mtf.confirms_htf:
            logger.info(
                "HOLD — MTF does not confirm HTF ({}) structure={}",
                htf.htf_bias,
                mtf.structure,
            )
            return TradingSignal.hold(
                f"MTF does not confirm HTF bias ({htf.htf_bias})"
            )

        # Step 4 — LTF
        ltf = await self.get_ltf_analysis(snapshot, htf, mtf)

        # Gate 3: LTF must present a valid entry trigger
        if not ltf.entry_trigger:
            logger.info(
                "HOLD — No LTF trigger, pattern={}",
                ltf.candle_pattern,
            )
            return TradingSignal.hold(
                f"No LTF entry trigger — {ltf.candle_pattern}"
            )

        # All 3 TF aligned → generate structured signal via tool_use
        system, user = build_signal_prompt(snapshot, macro, htf, mtf, ltf)
        try:
            resp = await self.provider.complete_structured(
                system, user, SIGNAL_TOOL
            )
            logger.debug(
                "signal | in={} out={}",
                resp.input_tokens,
                resp.output_tokens,
            )
            d = resp.tool_data
            signal = TradingSignal(
                action=d.get("action", "HOLD"),
                entry_price=float(d.get("entry_price", snapshot.mark_price)),
                stop_loss=float(d.get("stop_loss", 0.0)),
                tp1=float(d.get("tp1", 0.0)),
                tp2=float(d.get("tp2", 0.0)),
                tp3=float(d.get("tp3", 0.0)),
                htf_bias=d.get("htf_bias", htf.htf_bias),
                confidence=int(d.get("confidence", 0)),
                reasoning=str(d.get("reasoning", ""))[:500],
                mode=self.mode,
                provider=self.provider.provider_name,
                model=self.provider.model_name,
            )
            logger.info(
                "Signal: {} @ {} | conf={}% | {}/{}",
                signal.action,
                signal.entry_price,
                signal.confidence,
                self.provider.provider_name,
                self.provider.model_name,
            )
            return signal
        except Exception as e:
            logger.error("Signal generation failed: {}", e)
            return TradingSignal.hold(
                f"Signal generation error: {str(e)[:100]}"
            )

    # ------------------------------------------------------------------
    # Trade management
    # ------------------------------------------------------------------

    async def manage_trade(
        self, position: dict, snapshot: MarketSnapshot
    ) -> ManagementDecision:
        """Evaluate an open position and return a management decision.

        Args:
            position: Dict describing the open position. Expected keys:
                      ``side``, ``entry``, ``pnl_pct``, ``stop_loss``,
                      ``tp1``, ``tp2``, ``tp3``, ``duration``.
            snapshot: Current market snapshot.

        Returns:
            ManagementDecision with decision=HOLD|EXIT|ADJUST_SL.
            Defaults to HOLD on any API error.
        """
        system, user = build_management_prompt(position, snapshot)
        try:
            resp = await self.provider.complete_structured(
                system, user, MANAGEMENT_TOOL
            )
            logger.debug(
                "manage_trade | in={} out={}",
                resp.input_tokens,
                resp.output_tokens,
            )
            d = resp.tool_data
            decision = ManagementDecision(
                decision=d.get("decision", "HOLD"),
                new_stop_loss=(
                    float(d["new_stop_loss"])
                    if d.get("new_stop_loss") is not None
                    else None
                ),
                reasoning=str(d.get("reasoning", ""))[:300],
            )
            logger.info(
                "ManagementDecision: {} | {}",
                decision.decision,
                decision.reasoning[:80],
            )
            return decision
        except Exception as e:
            logger.warning("Trade management failed: {}", e)
            return ManagementDecision(
                decision="HOLD",
                reasoning=f"API error: {str(e)[:100]}",
            )

    # ------------------------------------------------------------------
    # Text parsers (regex-based for non-tool text responses)
    # ------------------------------------------------------------------

    def _parse_macro(self, text: str) -> MacroAnalysis:
        bias = self._extract(text, r"BIAS:\s*(\w+)", "NEUTRAL")
        conf = int(self._extract(text, r"CONFIDENCE:\s*(\d+)", "50"))
        risks = self._extract(text, r"RISKS:\s*(.+)", "N/A")
        return MacroAnalysis(
            bias=bias.upper(),
            confidence=min(max(conf, 0), 100),
            risks=risks,
            raw=text,
        )

    def _parse_htf(self, text: str) -> HTFAnalysis:
        bias = self._extract(text, r"HTF_BIAS:\s*([\w-]+)", "NEUTRAL")
        wave = self._extract(text, r"WAVE_POSITION:\s*(.+)", "unknown")
        sup_str = self._extract(
            text, r"KEY_SUPPORT:\s*([0-9.,\s]+)", ""
        )
        res_str = self._extract(
            text, r"KEY_RESISTANCE:\s*([0-9.,\s]+)", ""
        )
        inv = float(
            self._extract(text, r"INVALIDATION:\s*([0-9.]+)", "0")
        )
        conf = int(self._extract(text, r"CONFIDENCE:\s*(\d+)", "50"))

        support = [
            float(x.strip())
            for x in sup_str.split(",")
            if x.strip().replace(".", "").isdigit()
        ]
        resistance = [
            float(x.strip())
            for x in res_str.split(",")
            if x.strip().replace(".", "").isdigit()
        ]
        return HTFAnalysis(
            htf_bias=bias.upper(),
            wave_position=wave.strip(),
            key_support=support,
            key_resistance=resistance,
            invalidation=inv,
            confidence=min(max(conf, 0), 100),
            raw=text,
        )

    def _parse_mtf(self, text: str) -> MTFAnalysis:
        confirms = (
            self._extract(text, r"CONFIRMS_HTF:\s*(\w+)", "NO").upper() == "YES"
        )
        structure = self._extract(
            text, r"STRUCTURE:\s*(\w+)", "CONSOLIDATION"
        ).upper()
        zone_str = self._extract(
            text, r"ENTRY_ZONE:\s*([0-9.]+-[0-9.]+)", "0-0"
        )
        parts = zone_str.split("-")
        try:
            low = float(parts[0])
        except (ValueError, IndexError):
            low = 0.0
        try:
            high = float(parts[1])
        except (ValueError, IndexError):
            high = low
        reasoning = self._extract(text, r"REASONING:\s*(.+)", "N/A")
        return MTFAnalysis(
            confirms_htf=confirms,
            structure=structure,
            entry_zone_low=low,
            entry_zone_high=high,
            reasoning=reasoning.strip(),
            raw=text,
        )

    def _parse_ltf(self, text: str) -> LTFAnalysis:
        trigger = (
            self._extract(text, r"ENTRY_TRIGGER:\s*(\w+)", "NO").upper() == "YES"
        )
        pattern = self._extract(text, r"CANDLE_PATTERN:\s*(.+)", "none")
        try:
            entry = float(
                self._extract(text, r"ENTRY_PRICE:\s*([0-9.]+)", "0")
            )
        except ValueError:
            entry = 0.0
        reasoning = self._extract(text, r"REASONING:\s*(.+)", "N/A")
        return LTFAnalysis(
            entry_trigger=trigger,
            candle_pattern=pattern.strip().lower(),
            entry_price=entry,
            reasoning=reasoning.strip(),
            raw=text,
        )

    @staticmethod
    def _extract(text: str, pattern: str, default: str) -> str:
        """Extract the first capture group from text using the given regex.

        Args:
            text:    Source text.
            pattern: Regex pattern with one capture group.
            default: Value returned when no match is found.

        Returns:
            Stripped matched group or default.
        """
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else default
