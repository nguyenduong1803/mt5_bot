import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

load_dotenv()

# Default MetaTrader 5 terminal install location. Override with the
# MT5_TERMINAL_PATH env var if the terminal is installed elsewhere.
DEFAULT_MT5_TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def _config_path() -> Path:
    return Path(os.getenv("CONFIG_PATH", "config.json"))


def get_mt5_terminal_path(strategy: Optional["MT5Config"] = None) -> str:
    """Returns the MT5 terminal path for a strategy.

    Priority:
    1. Per-strategy terminal_path in config
    2. MT5_TERMINAL_PATH env var
    3. Default installation path
    """
    if strategy is not None and strategy.terminal_path:
        return strategy.terminal_path
    return os.getenv("MT5_TERMINAL_PATH", DEFAULT_MT5_TERMINAL_PATH)


class MT5Config(BaseModel):
    login: int
    server: str
    # Path to terminal64.exe for this broker. Each broker (Exness, FTMO, etc.)
    # has its own MT5 installation with its own server list. If not set,
    # falls back to MT5_TERMINAL_PATH env var or default installation path.
    terminal_path: Optional[str] = None


class TelegramConfig(BaseModel):
    enabled: bool = True
    # Chat/group/channel id to notify for this strategy.
    chatId: str
    # Bot token for this strategy. Optional here — if omitted, falls back
    # to TELEGRAM_BOT_TOKEN_<STRATEGY> or TELEGRAM_BOT_TOKEN in .env.
    botToken: Optional[str] = None

class StrategyConfig(BaseModel):
    # Base capital allocated per full-size (order_ratio=1) trade, in USD.
    # investment = price * order_ratio.
    price: float
    # Starting max allowed slippage (points) between requested and filled
    # price. Kept moderate on purpose — the order pipeline auto-widens
    # this on retry (see mt5_client.send_order_with_retry) if the broker
    # rejects the fill for being too far from this, so a real price spike
    # still gets filled without accepting unlimited slippage up front.
    deviation: int = 200
    magic: int
    comment: str = ""
    mt5: MT5Config
    telegram: Optional[TelegramConfig] = None
    dryRun: Optional[bool] = None

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("price must be greater than 0")
        return v


class AppConfig(BaseModel):
    dryRun: bool = True
    strategies: Dict[str, StrategyConfig] = Field(default_factory=dict)


class ConfigError(Exception):
    pass


def _load_raw_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config file {path}: {e}") from e

    try:
        return AppConfig.model_validate(data)
    except Exception as e:
        raise ConfigError(f"Invalid config schema in {path}: {e}") from e


@lru_cache
def get_config() -> AppConfig:
    return _load_raw_config(_config_path())


def reload_config() -> AppConfig:
    get_config.cache_clear()
    return get_config()


def get_strategy_config(strategy_name: str) -> StrategyConfig:
    config = get_config()
    strategy = config.strategies.get(strategy_name)
    if strategy is None:
        raise ConfigError(f"Unknown strategy: {strategy_name}")
    return strategy


def is_dry_run(strategy: StrategyConfig) -> bool:
    env_override = os.getenv("DRY_RUN")
    if env_override is not None:
        return env_override.strip().lower() in ("1", "true", "yes", "on")
    if strategy.dryRun is not None:
        return strategy.dryRun
    return get_config().dryRun


def get_mt5_password(strategy_name: str) -> str:
    env_key = f"MT5_PASSWORD_{strategy_name.upper()}"
    password = os.getenv(env_key)
    if not password:
        raise ConfigError(
            f"Missing MT5 password for strategy '{strategy_name}'. "
            f"Set {env_key} in .env"
        )
    return password


def get_telegram_bot_token(strategy_name: str) -> Optional[str]:
    """Per-strategy bot token (TELEGRAM_BOT_TOKEN_<STRATEGY>) takes
    precedence over the global TELEGRAM_BOT_TOKEN, so different strategies
    can notify via different bots if needed."""
    per_strategy_key = f"TELEGRAM_BOT_TOKEN_{strategy_name.upper()}"
    return os.getenv(per_strategy_key) or os.getenv("TELEGRAM_BOT_TOKEN")
