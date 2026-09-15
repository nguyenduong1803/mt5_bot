"""Thin wrapper around the MetaTrader5 package.

Kept isolated from business logic so it can be mocked easily in tests
(the real `MetaTrader5` package only works on Windows with a running
terminal).
"""
import threading
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

import platform as _platform
import sys as _sys

if _platform.system() == "Windows":
    import MetaTrader5 as mt5
else:
    # Linux/Docker: MetaTrader5 is a Windows-only DLL. Prefer an injected
    # fake (tests install one into sys.modules) so the suite can run
    # without the Wine HTTP proxy; otherwise forward via mt5_linux_client.
    _injected = _sys.modules.get("MetaTrader5")
    if _injected is not None and getattr(_injected, "_is_fake", False):
        mt5 = _injected  # type: ignore[assignment]
    else:
        from app import mt5_linux_client as mt5  # type: ignore[assignment]

from app.config import ResolvedAccountConfig, get_mt5_terminal_path
from app.logging_config import logger

# The MetaTrader5 Python API maintains a single connection per process and
# is not safe for concurrent calls. Every function in this module that
# talks to the terminal (connect, read symbol/tick, send/close orders)
# must be called while holding this lock, so two webhook requests arriving
# at the same time can never interleave their MT5 calls (e.g. one
# strategy's account swapping out another's mid-order).
MT5_LOCK = threading.RLock()

# Bitmask values for symbol_info.filling_mode (not exposed as constants by
# the MetaTrader5 package). See SYMBOL_FILLING_MODE in the MQL5 docs.
_SYMBOL_FILLING_FOK = 1
_SYMBOL_FILLING_IOC = 2

# retcodes worth a fresh-price retry: the broker re-quoted, or the price
# moved between us reading the tick and the order reaching the server.
RETRYABLE_RETCODES = {mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_CHANGED}


class MT5Error(Exception):
    pass


@dataclass
class TerminalIdentity:
    login: int
    server: str
    path: str


_current_terminal: Optional[TerminalIdentity] = None


def _last_error_str() -> str:
    code, description = mt5.last_error()
    return f"({code}) {description}"


def ensure_connection(resolved: ResolvedAccountConfig) -> None:
    """(Re)connects to the MT5 terminal required by this resolved account.

    MetaTrader5's Python API talks to a single terminal instance per
    process, so we only re-initialize when the target account/terminal
    actually changes. Callers must hold MT5_LOCK.

    Each broker (Exness, FTMO, etc.) has its own MT5 terminal installation
    with its own server list. The terminal path is resolved from:
    1. resolved.mt5.terminal_path (per-account config)
    2. MT5_TERMINAL_PATH env var
    3. Default installation path
    """
    global _current_terminal

    terminal_path = get_mt5_terminal_path(resolved.mt5)
    login = resolved.mt5.l
    password = resolved.mt5.p
    server = resolved.mt5.server
    identity = TerminalIdentity(login=login, server=server, path=terminal_path)

    if _current_terminal == identity:
        # Already connected to the right account; verify the terminal is alive.
        if mt5.terminal_info() is not None:
            return

    ok = mt5.initialize(
        path=terminal_path,
        login=login,
        password=password,
        server=server,
    )
    if not ok:
        error = _last_error_str()
        _current_terminal = None
        raise MT5Error(f"Failed to initialize MT5 terminal: {error}")

    _current_terminal = identity
    logger.info(
        "Connected to MT5 terminal login=%s server=%s path=%s",
        login,
        server,
        terminal_path,
    )


def shutdown() -> None:
    global _current_terminal
    mt5.shutdown()
    _current_terminal = None


def get_symbol_info(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        raise MT5Error(f"Symbol '{symbol}' not found on this MT5 terminal")
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            raise MT5Error(f"Failed to select symbol '{symbol}' in Market Watch")
        info = mt5.symbol_info(symbol)
    return info


def ensure_symbol_tradable(symbol_info) -> None:
    if symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
        raise MT5Error(
            f"Symbol '{symbol_info.name}' trading is disabled on this account "
            f"(market closed, or broker restricted this symbol)"
        )


def resolve_filling_mode(symbol_info) -> int:
    """Picks an order filling type the symbol/broker actually supports.

    A hardcoded filling mode is the single most common cause of silently
    rejected orders (retcode 10030 "Unsupported filling mode") — brokers
    differ in which of FOK/IOC/RETURN they accept per symbol.
    """
    mode = symbol_info.filling_mode
    if mode & _SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    if mode & _SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def normalize_volume(symbol_info, volume: float) -> float:
    """Rounds `volume` to the symbol's lot step and clamps to min/max.

    Uses Decimal (not float rounding) because volume_step is frequently a
    non-power-of-10 value (e.g. 0.05 lots on some index/metal symbols),
    where float rounding can silently drift off the actual step grid.
    """
    step = Decimal(str(symbol_info.volume_step or 0.01))
    vol_min = Decimal(str(symbol_info.volume_min))
    vol_max = Decimal(str(symbol_info.volume_max))
    raw = Decimal(str(volume))

    steps = (raw / step).to_integral_value(rounding=ROUND_HALF_UP)
    normalized = steps * step
    normalized = max(vol_min, min(vol_max, normalized))
    return float(normalized)


def get_open_positions(symbol: str, magic: int) -> List:
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return []
    return [p for p in positions if p.magic == magic]


def get_tick(symbol: str, max_wait_seconds: float = 3.0, poll_interval: float = 0.2):
    """Returns the latest tick, waiting briefly for a real (non-zero) quote.

    Right after a symbol is added to Market Watch (see get_symbol_info),
    MT5 can return a tick with bid/ask still at 0 until the first live
    quote arrives from the broker — using that as a price would divide by
    zero downstream, so this waits for a valid quote instead of failing
    on the very first check.
    """
    deadline = time.monotonic() + max_wait_seconds
    tick = None
    while True:
        tick = mt5.symbol_info_tick(symbol)
        if tick is not None and tick.bid > 0 and tick.ask > 0:
            return tick
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

    if tick is None:
        raise MT5Error(f"Failed to get tick for symbol '{symbol}'")
    raise MT5Error(
        f"No live price yet for symbol '{symbol}' (bid={tick.bid}, ask={tick.ask}) "
        f"after waiting {max_wait_seconds}s — market may be closed or the "
        f"symbol was just added to Market Watch"
    )


def send_order(request: dict):
    result = mt5.order_send(request)
    if result is None:
        raise MT5Error(f"order_send returned None: {_last_error_str()}")
    return result


def send_order_with_retry(
    build_request,
    symbol: str,
    base_deviation: int,
    max_attempts: int = 5,
    deviation_growth: int = 3,
    max_deviation: Optional[int] = None,
    retry_delay_seconds: float = 0.25,
):
    """Sends an order, retrying with a fresh tick on requote/price-changed.

    `build_request(tick, deviation)` must return the MT5 request dict for a
    given tick and deviation (points) — called again on each retry so the
    price is always current. The deviation starts tight (`base_deviation`,
    protecting the fill price) and widens by `deviation_growth`x on each
    retry — capped at `max_deviation` (defaults to 25x the base) so a
    volatile spike still gets multiple real chances to fill without the
    tolerance growing unbounded. A short delay between retries lets the
    next tick actually be a *different*, fresher quote instead of hammering
    the server on the same stale one.

    Only requote/price-changed rejections are retried — anything else
    (invalid volume, market closed, trading disabled, no money, ...) is a
    structural rejection retrying won't fix, so it's returned immediately.
    """
    if max_deviation is None:
        max_deviation = base_deviation * 25

    result = None
    deviation = base_deviation
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            time.sleep(retry_delay_seconds)

        tick = get_tick(symbol)
        request = build_request(tick, deviation)
        result = send_order(request)

        if result.retcode not in RETRYABLE_RETCODES:
            return result

        logger.warning(
            "Order got retcode=%s (%s) on attempt %d/%d for symbol=%s "
            "(deviation=%d), retrying with fresh price and wider deviation",
            result.retcode,
            result.comment,
            attempt,
            max_attempts,
            symbol,
            deviation,
        )
        deviation = min(deviation * deviation_growth, max_deviation)
    return result


ORDER_TYPE_BUY = mt5.ORDER_TYPE_BUY
ORDER_TYPE_SELL = mt5.ORDER_TYPE_SELL
TRADE_ACTION_DEAL = mt5.TRADE_ACTION_DEAL
ORDER_TIME_GTC = mt5.ORDER_TIME_GTC
ORDER_FILLING_IOC = mt5.ORDER_FILLING_IOC
ORDER_FILLING_FOK = mt5.ORDER_FILLING_FOK
ORDER_FILLING_RETURN = mt5.ORDER_FILLING_RETURN
TRADE_RETCODE_DONE = mt5.TRADE_RETCODE_DONE
TRADE_RETCODE_REQUOTE = mt5.TRADE_RETCODE_REQUOTE
TRADE_RETCODE_PRICE_CHANGED = mt5.TRADE_RETCODE_PRICE_CHANGED
POSITION_TYPE_BUY = mt5.POSITION_TYPE_BUY
POSITION_TYPE_SELL = mt5.POSITION_TYPE_SELL
