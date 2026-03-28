from dataclasses import dataclass, field
from dotenv import load_dotenv
import os

load_dotenv()


@dataclass
class Settings:
    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_chat_id: int = field(
        default_factory=lambda: int(os.getenv("TELEGRAM_CHAT_ID", "0"))
    )

    # ------------------------------------------------------------------
    # Binance
    # ------------------------------------------------------------------
    binance_api_key: str = field(
        default_factory=lambda: os.getenv("BINANCE_API_KEY", "")
    )
    binance_secret_key: str = field(
        default_factory=lambda: os.getenv("BINANCE_SECRET_KEY", "")
    )
    binance_testnet: bool = field(
        default_factory=lambda: os.getenv("BINANCE_TESTNET", "true").lower() == "true"
    )

    # ------------------------------------------------------------------
    # LLM — default provider / model
    # ------------------------------------------------------------------
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic")
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    )
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )
    gemini_api_key: str = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", "")
    )
    deepseek_api_key: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "")
    )

    # ------------------------------------------------------------------
    # Per-mode LLM overrides (optional; empty string = use default)
    # ------------------------------------------------------------------
    llm_provider_swing: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER_SWING", "")
    )
    llm_model_swing: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL_SWING", "")
    )
    llm_provider_intraday: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER_INTRADAY", "")
    )
    llm_model_intraday: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL_INTRADAY", "")
    )
    llm_provider_scalp: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER_SCALP", "")
    )
    llm_model_scalp: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL_SCALP", "")
    )

    # ------------------------------------------------------------------
    # Macro data sources
    # ------------------------------------------------------------------
    fred_api_key: str = field(
        default_factory=lambda: os.getenv("FRED_API_KEY", "")
    )
    news_api_key: str = field(
        default_factory=lambda: os.getenv("NEWS_API_KEY", "")
    )

    # ------------------------------------------------------------------
    # Bot runtime settings
    # ------------------------------------------------------------------
    paper_trade: bool = field(
        default_factory=lambda: os.getenv("PAPER_TRADE", "true").lower() == "true"
    )
    trading_mode: str = field(
        default_factory=lambda: os.getenv("TRADING_MODE", "swing")
    )
    auto_trade: bool = field(
        default_factory=lambda: os.getenv("AUTO_TRADE", "false").lower() == "true"
    )
    dry_run: bool = field(
        default_factory=lambda: os.getenv("DRY_RUN", "false").lower() == "true"
    )
    db_path: str = field(
        default_factory=lambda: os.getenv("DB_PATH", "data/bot.db")
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------
    health_verbose: bool = field(
        default_factory=lambda: os.getenv("HEALTH_VERBOSE", "false").lower() == "true"
    )
    health_interval_min: int = field(
        default_factory=lambda: int(os.getenv("HEALTH_INTERVAL_MIN", "5"))
    )
    health_alert_after_min: int = field(
        default_factory=lambda: int(os.getenv("HEALTH_ALERT_AFTER_MIN", "15"))
    )

    # ------------------------------------------------------------------
    # Cost controls
    # ------------------------------------------------------------------
    llm_daily_budget_usd: float = field(
        default_factory=lambda: float(os.getenv("LLM_DAILY_BUDGET_USD", "5.0"))
    )
    min_position_usd: float = field(
        default_factory=lambda: float(os.getenv("MIN_POSITION_USD", "10.0"))
    )

    # ------------------------------------------------------------------
    # Per-mode helpers
    # ------------------------------------------------------------------
    def get_llm_provider_for_mode(self, mode: str) -> str:
        """Return mode-specific LLM provider, falling back to default."""
        override = getattr(self, f"llm_provider_{mode}", "")
        return override if override else self.llm_provider

    def get_llm_model_for_mode(self, mode: str) -> str:
        """Return mode-specific LLM model, falling back to default."""
        override = getattr(self, f"llm_model_{mode}", "")
        return override if override else self.llm_model

    # ------------------------------------------------------------------
    # Validation (call explicitly; not auto-run to allow partial configs
    # during testing/development)
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """Raise ValueError listing all missing/invalid config values."""
        errors: list[str] = []

        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN missing")
        if not self.telegram_chat_id:
            errors.append("TELEGRAM_CHAT_ID missing")
        if not self.binance_api_key:
            errors.append("BINANCE_API_KEY missing")
        if not self.binance_secret_key:
            errors.append("BINANCE_SECRET_KEY missing")

        # At least one LLM key must be provided
        if not any([
            self.anthropic_api_key,
            self.openai_api_key,
            self.gemini_api_key,
            self.deepseek_api_key,
        ]):
            errors.append("At least one LLM API key required (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, or DEEPSEEK_API_KEY)")

        if self.trading_mode not in ("swing", "intraday", "scalp"):
            errors.append(f"TRADING_MODE must be swing|intraday|scalp, got '{self.trading_mode}'")

        if self.llm_daily_budget_usd <= 0:
            errors.append("LLM_DAILY_BUDGET_USD must be > 0")

        if self.min_position_usd <= 0:
            errors.append("MIN_POSITION_USD must be > 0")

        if errors:
            raise ValueError(f"Config validation errors: {'; '.join(errors)}")


# Module-level singleton — import this everywhere.
# NOTE: validate() is NOT called here so the bot can still import config
#       during local development without a full .env file.
settings = Settings()
