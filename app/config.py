"""
Central configuration for the trading bot.

Design decisions:
- All configuration is read from environment variables (via a .env file in
  development) and validated with pydantic. Nothing here is hardcoded to a
  specific broker, symbol suffix, or account.
- `TradingMode` is the single safety-critical setting in this file. The
  validator below makes it structurally impossible to start in LIVE mode
  without an explicit, separate confirmation flag. This is enforced again
  later by the execution engine (defense in depth) - config validation
  alone must never be the only gate against accidental live trading.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    DEMO = "DEMO"
    LIVE = "LIVE"


LIVE_CONFIRMATION_PHRASE = "YES_I_UNDERSTAND_THE_RISK"


def _parse_symbol_map(raw: str) -> dict[str, str]:
    """Parse 'EURUSD,XAUUSD:XAUUSDm' into {'EURUSD': 'EURUSD', 'XAUUSD': 'XAUUSDm'}."""
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            canonical, broker = entry.split(":", 1)
        else:
            canonical, broker = entry, entry
        mapping[canonical.strip().upper()] = broker.strip()
    return mapping


def _parse_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_sessions(raw: str) -> list[tuple[str, str]]:
    sessions: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        start, end = entry.split("-")
        sessions.append((start.strip(), end.strip()))
    return sessions


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- MT5 connection ---
    mt5_path: str | None = Field(default=None, alias="MT5_PATH")
    mt5_login: int | None = Field(default=None, alias="MT5_LOGIN")
    mt5_password: str | None = Field(default=None, alias="MT5_PASSWORD")
    mt5_server: str | None = Field(default=None, alias="MT5_SERVER")

    # --- Trading mode / safety ---
    trading_mode: TradingMode = Field(default=TradingMode.DRY_RUN, alias="TRADING_MODE")
    live_trading_confirmed: str = Field(default="", alias="LIVE_TRADING_CONFIRMED")

    # --- Symbols / timeframes ---
    symbols_raw: str = Field(default="EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,NZDUSD,XAUUSD", alias="SYMBOLS")
    primary_timeframes_raw: str = Field(default="M15,H1", alias="PRIMARY_TIMEFRAMES")
    trend_confirmation_timeframe: str = Field(default="H4", alias="TREND_CONFIRMATION_TIMEFRAME")

    # --- Risk management ---
    risk_per_trade: float = Field(default=0.01, alias="RISK_PER_TRADE")
    max_daily_loss: float = Field(default=0.03, alias="MAX_DAILY_LOSS")
    max_weekly_loss: float = Field(default=0.08, alias="MAX_WEEKLY_LOSS")
    max_open_trades: int = Field(default=3, alias="MAX_OPEN_TRADES")
    max_trades_per_day: int = Field(default=10, alias="MAX_TRADES_PER_DAY")
    max_exposure_per_currency: float = Field(default=0.03, alias="MAX_EXPOSURE_PER_CURRENCY")
    max_spread_points: float = Field(default=25, alias="MAX_SPREAD_POINTS")
    min_risk_reward: float = Field(default=2.0, alias="MIN_RISK_REWARD")
    max_drawdown_halt: float = Field(default=0.10, alias="MAX_DRAWDOWN_HALT")

    # --- Sessions ---
    trading_sessions_raw: str = Field(default="", alias="TRADING_SESSIONS")

    # --- News filter ---
    news_filter_enabled: bool = Field(default=False, alias="NEWS_FILTER_ENABLED")
    news_provider: str = Field(default="none", alias="NEWS_PROVIDER")
    news_api_key: str | None = Field(default=None, alias="NEWS_API_KEY")
    news_blackout_before_min: int = Field(default=30, alias="NEWS_BLACKOUT_BEFORE_MIN")
    news_blackout_after_min: int = Field(default=15, alias="NEWS_BLACKOUT_AFTER_MIN")

    # --- Telegram ---
    telegram_enabled: bool = Field(default=False, alias="TELEGRAM_ENABLED")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")

    # --- LLM analysis layer ---
    llm_enabled: bool = Field(default=False, alias="LLM_ENABLED")
    llm_api_base: str = Field(default="https://api.openai.com/v1", alias="LLM_API_BASE")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    # --- Database ---
    database_url: str = Field(default="sqlite:///./data/trading_bot.db", alias="DATABASE_URL")

    # --- API server ---
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    # Optional shared-secret auth for the dashboard API. Unset (the
    # default) means no auth is enforced - fine when api_host is the
    # 127.0.0.1 default, since nothing off-box can reach it. Set this
    # (and send it back as the X-API-Key header) before ever binding
    # API_HOST to a non-loopback address, or any device on the network
    # could trip/reset the emergency stop or trigger backtests/ML
    # training on demand with no authentication at all.
    api_key: str | None = Field(default=None, alias="API_KEY")

    # --- Logging ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: str = Field(default="./logs", alias="LOG_DIR")

    # --- Execution ---
    magic_number: int = Field(default=990011, alias="MAGIC_NUMBER")

    # ---- Derived / validated properties ----

    @property
    def symbol_map(self) -> dict[str, str]:
        return _parse_symbol_map(self.symbols_raw)

    @property
    def primary_timeframes(self) -> list[str]:
        return _parse_list(self.primary_timeframes_raw)

    @property
    def trading_sessions(self) -> list[tuple[str, str]]:
        return _parse_sessions(self.trading_sessions_raw)

    @field_validator("risk_per_trade")
    @classmethod
    def _validate_risk_per_trade(cls, v: float) -> float:
        if not (0 < v <= 0.05):
            raise ValueError("RISK_PER_TRADE must be between 0 and 0.05 (5%). Refusing unsafe config.")
        return v

    @field_validator("max_daily_loss", "max_weekly_loss", "max_drawdown_halt")
    @classmethod
    def _validate_loss_limits(cls, v: float) -> float:
        if not (0 < v <= 0.5):
            raise ValueError("Loss/drawdown limits must be between 0 and 0.5 (50%).")
        return v

    @field_validator("min_risk_reward")
    @classmethod
    def _validate_min_rr(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError("MIN_RISK_REWARD must be >= 1.0. Trades with worse than 1:1 R:R are not permitted.")
        return v

    @model_validator(mode="after")
    def _validate_live_mode_safety(self) -> "Settings":
        """
        Hard safety gate: LIVE mode requires an explicit, separately-set
        confirmation phrase. This cannot be satisfied by TRADING_MODE alone,
        so a typo or a stray "LIVE" in an env file cannot silently enable
        live trading.
        """
        if self.trading_mode == TradingMode.LIVE:
            if self.live_trading_confirmed != LIVE_CONFIRMATION_PHRASE:
                raise ValueError(
                    "TRADING_MODE=LIVE requires LIVE_TRADING_CONFIRMED="
                    f'"{LIVE_CONFIRMATION_PHRASE}" to be set explicitly. Refusing to start.'
                )
        return self

    @model_validator(mode="after")
    def _validate_api_auth_required_for_non_local_binding(self) -> "Settings":
        """
        Security review finding: the dashboard API has no built-in auth,
        which is fine only because it binds to 127.0.0.1 by default -
        nothing off-box can reach it. The moment API_HOST is changed to
        listen on a non-loopback address, an API_KEY becomes mandatory;
        without this, anyone on the network could trip/reset the
        emergency stop or trigger backtests/ML training with zero
        authentication.
        """
        if self.api_host not in ("127.0.0.1", "localhost", "::1") and not self.api_key:
            raise ValueError(
                f"API_HOST={self.api_host!r} binds beyond localhost, which requires API_KEY to be set "
                "(and sent back as the X-API-Key header) - refusing to start unauthenticated on a "
                "non-loopback address."
            )
        return self

    def ensure_directories(self) -> None:
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        Path("data").mkdir(parents=True, exist_ok=True)
        Path("models").mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached settings accessor. Use this instead of instantiating Settings() directly."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """Force re-read of environment/.env - useful for tests."""
    global _settings
    _settings = Settings()
    return _settings
