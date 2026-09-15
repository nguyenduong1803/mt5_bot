import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

load_dotenv()

# Default MetaTrader 5 terminal install location. Override with the
# MT5_TERMINAL_PATH env var if the terminal is installed elsewhere.
DEFAULT_MT5_TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def _config_path() -> Path:
    return Path(os.getenv("CONFIG_PATH", "config.json"))


def get_mt5_terminal_path(mt5: Optional["MT5Config"] = None) -> str:
    """Returns the MT5 terminal path for an account.

    Priority:
    1. Per-account terminal_path in config
    2. MT5_TERMINAL_PATH env var
    3. Default installation path
    """
    if mt5 is not None and mt5.terminal_path:
        return mt5.terminal_path
    return os.getenv("MT5_TERMINAL_PATH", DEFAULT_MT5_TERMINAL_PATH)


class MT5Config(BaseModel):
    l: int
    p: str
    server: str
    # Path to terminal64.exe for this broker. Each broker (Exness, FTMO, etc.)
    # has its own MT5 installation with its own server list. If not set,
    # falls back to MT5_TERMINAL_PATH env var or default installation path.
    terminal_path: Optional[str] = None


class AccountConfig(BaseModel):
    enabled: bool = True
    magic: int
    price: Optional[float] = None
    deviation: Optional[int] = None
    comment: Optional[str] = None
    dryRun: Optional[bool] = None
    mt5: MT5Config


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
    comment: str = ""
    dryRun: Optional[bool] = None
    accounts: Dict[str, AccountConfig]

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("price must be greater than 0")
        return v


class ResolvedAccountConfig(BaseModel):
    account_key: str
    strategy_name: str
    price: float
    deviation: int
    magic: int
    comment: str
    dryRun: Optional[bool]
    mt5: MT5Config


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


def resolve_account_config(strategy_name: str, account_key: str) -> ResolvedAccountConfig:
    strategy = get_strategy_config(strategy_name)
    account = strategy.accounts.get(account_key)
    if account is None:
        raise ConfigError(
            f"unknown_account: Unknown account '{account_key}' for strategy '{strategy_name}'"
        )
    if not account.enabled:
        raise ConfigError(
            f"account_disabled: Account '{account_key}' is disabled for strategy '{strategy_name}'"
        )
    return ResolvedAccountConfig(
        account_key=account_key,
        strategy_name=strategy_name,
        price=account.price if account.price is not None else strategy.price,
        deviation=account.deviation if account.deviation is not None else strategy.deviation,
        magic=account.magic,
        comment=account.comment if account.comment is not None else strategy.comment,
        dryRun=account.dryRun if account.dryRun is not None else strategy.dryRun,
        mt5=account.mt5,
    )


def resolve_execution_targets(
    strategy_name: str, account: Optional[str]
) -> List[ResolvedAccountConfig]:
    strategy = get_strategy_config(strategy_name)
    if account is not None:
        return [resolve_account_config(strategy_name, account)]
    enabled = [k for k, a in strategy.accounts.items() if a.enabled]
    if not enabled:
        raise ConfigError(
            f"no_enabled_accounts: No enabled accounts for strategy '{strategy_name}'"
        )
    return [resolve_account_config(strategy_name, k) for k in enabled]


def is_dry_run(resolved: ResolvedAccountConfig) -> bool:
    env_override = os.getenv("DRY_RUN")
    if env_override is not None:
        return env_override.strip().lower() in ("1", "true", "yes", "on")
    if resolved.dryRun is not None:
        return resolved.dryRun
    return get_config().dryRun


def get_telegram_bot_token() -> Optional[str]:
    return os.getenv("TELEGRAM_BOT_TOKEN")


def get_telegram_chat_id() -> Optional[str]:
    return os.getenv("TELEGRAM_CHAT_ID")
