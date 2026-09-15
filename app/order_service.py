from functools import partial
from typing import List

from app import mt5_client
from app.config import ConfigError, ResolvedAccountConfig, is_dry_run
from app.logging_config import logger
from app.mt5_client import MT5Error
from app.schemas import AccountOrderResult, OrderId, OrderResult, TradingViewAlert
from app.symbols import strip_perpetual_suffix


class OrderExecutionError(Exception):
    pass


def _compute_investment(resolved: ResolvedAccountConfig, order_ratio: float) -> float:
    return resolved.price * order_ratio


def _build_close_request(
    position, resolved: ResolvedAccountConfig, symbol: str, filling_mode, tick, deviation: int
) -> dict:
    if position.type == mt5_client.POSITION_TYPE_BUY:
        order_type = mt5_client.ORDER_TYPE_SELL
        close_price = tick.bid
    else:
        order_type = mt5_client.ORDER_TYPE_BUY
        close_price = tick.ask

    return {
        "action": mt5_client.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": position.volume,
        "type": order_type,
        "position": position.ticket,
        "price": close_price,
        "deviation": deviation,
        "magic": resolved.magic,
        "comment": resolved.comment,
        "type_time": mt5_client.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }


def _build_open_request(
    order_type, volume: float, resolved: ResolvedAccountConfig, symbol: str, filling_mode, tick, deviation: int
) -> dict:
    price = tick.ask if order_type == mt5_client.ORDER_TYPE_BUY else tick.bid

    return {
        "action": mt5_client.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": deviation,
        "magic": resolved.magic,
        "comment": resolved.comment,
        "type_time": mt5_client.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }


# Max time to wait to acquire MT5_LOCK before giving up. Without this, a
# single stuck call (MT5 terminal frozen on a dialog, a hung IPC connect...)
# would hold the lock forever and make every subsequent webhook call hang
# indefinitely waiting for it, instead of failing with a clear error.
LOCK_TIMEOUT_SECONDS = 90


def _lock_timeout_error() -> OrderExecutionError:
    return OrderExecutionError(
        f"Timed out after {LOCK_TIMEOUT_SECONDS}s waiting for another "
        "order to finish using the MT5 connection. If this keeps "
        "happening, the MT5 terminal is likely stuck (a dialog waiting "
        "to be dismissed, or a broken connection) — restart the bot "
        "and check the terminal."
    )


def execute_order_for_account(
    alert: TradingViewAlert,
    resolved: ResolvedAccountConfig,
    _lock_held: bool = False,
) -> AccountOrderResult:
    """Executes one webhook alert end-to-end against a single MT5 account.

    Runs under MT5_LOCK unless the caller already holds it (batch path
    passes `_lock_held=True`). The MetaTrader5 API is a single connection
    per process, so concurrent webhook requests must never interleave
    their MT5 calls.
    """
    if not _lock_held:
        if not mt5_client.MT5_LOCK.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            raise _lock_timeout_error()
    try:
        dry_run = is_dry_run(resolved)
        # TradingView's perpetual-futures ticker suffix (".P", e.g.
        # "BTCUSDT.P") never matches a real MT5 symbol name, so it's
        # stripped before this is used for anything MT5-related.
        symbol = strip_perpetual_suffix(alert.symbol)

        try:
            # Real market prices (for volume sizing and order pricing)
            # always come from the MT5 terminal, even in dry-run mode, so
            # connection is required regardless of dry_run.
            mt5_client.ensure_connection(resolved)

            if alert.order_id == OrderId.OPEN_LONG:
                # Reverse: an opposite (SHORT) position open for this
                # symbol/magic gets closed first, so openLong always ends
                # up LONG-only, never both sides open at once.
                reversal_note = _close_opposite_positions(
                    resolved, symbol, mt5_client.POSITION_TYPE_SELL, dry_run
                )
                return _open_position(
                    alert, resolved, symbol, mt5_client.ORDER_TYPE_BUY, dry_run, reversal_note
                )
            elif alert.order_id == OrderId.OPEN_SHORT:
                reversal_note = _close_opposite_positions(
                    resolved, symbol, mt5_client.POSITION_TYPE_BUY, dry_run
                )
                return _open_position(
                    alert, resolved, symbol, mt5_client.ORDER_TYPE_SELL, dry_run, reversal_note
                )
            elif alert.order_id == OrderId.CLOSE_LONG:
                return _close_positions(
                    alert, resolved, symbol, mt5_client.POSITION_TYPE_BUY, dry_run
                )
            elif alert.order_id == OrderId.CLOSE_SHORT:
                return _close_positions(
                    alert, resolved, symbol, mt5_client.POSITION_TYPE_SELL, dry_run
                )
            elif alert.order_id == OrderId.CLOSE_ALL:
                return _close_all_positions(alert, resolved, symbol, dry_run)
            else:
                raise OrderExecutionError(f"Unsupported order_id: {alert.order_id}")
        except MT5Error as e:
            logger.error("MT5 error while executing order: %s", e)
            raise OrderExecutionError(str(e)) from e
    finally:
        if not _lock_held:
            mt5_client.MT5_LOCK.release()


def execute_order_batch(
    alert: TradingViewAlert, targets: List[ResolvedAccountConfig]
) -> List[AccountOrderResult]:
    """Runs the same alert against multiple accounts under one MT5_LOCK.

    Per-account failures become success=False results; remaining accounts
    still execute.
    """
    if not mt5_client.MT5_LOCK.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        raise _lock_timeout_error()

    results: List[AccountOrderResult] = []
    try:
        for resolved in targets:
            try:
                results.append(
                    execute_order_for_account(alert, resolved, _lock_held=True)
                )
            except (OrderExecutionError, MT5Error, ConfigError) as e:
                results.append(
                    AccountOrderResult(
                        account=resolved.account_key,
                        success=False,
                        dry_run=is_dry_run(resolved),
                        strategy=resolved.strategy_name,
                        symbol=strip_perpetual_suffix(alert.symbol),
                        action=alert.order_id.value,
                        message=str(e),
                    )
                )
    finally:
        mt5_client.MT5_LOCK.release()
    return results


def _side_name(position_type) -> str:
    return "LONG" if position_type == mt5_client.POSITION_TYPE_BUY else "SHORT"


def _close_all_positions(
    alert: TradingViewAlert, resolved: ResolvedAccountConfig, symbol: str, dry_run: bool
) -> OrderResult:
    """Closes ALL open positions for this symbol/magic regardless of direction."""
    positions = mt5_client.get_open_positions(symbol, resolved.magic)

    if not positions:
        message = f"No open positions found for symbol={symbol} magic={resolved.magic}"
        logger.warning(message)
        return OrderResult(
            account=resolved.account_key,
            success=False,
            dry_run=dry_run,
            strategy=alert.strategy,
            symbol=symbol,
            action=alert.order_id.value,
            message=message,
        )

    total_volume = sum(p.volume for p in positions)
    long_count = sum(1 for p in positions if p.type == mt5_client.POSITION_TYPE_BUY)
    short_count = len(positions) - long_count

    if dry_run:
        logger.info(
            "[DRY RUN] strategy=%s symbol=%s action=closeAll would close %d position(s) volume=%s",
            alert.strategy,
            symbol,
            len(positions),
            total_volume,
        )
        return OrderResult(
            account=resolved.account_key,
            success=True,
            dry_run=True,
            strategy=alert.strategy,
            symbol=symbol,
            action=alert.order_id.value,
            volume=total_volume,
            message=f"Dry run: would close {len(positions)} position(s) (L:{long_count}/S:{short_count}), no order sent to MT5",
        )

    logger.info(
        "Closing ALL positions: strategy=%s symbol=%s count=%d (L:%d/S:%d) volume=%s",
        alert.strategy,
        symbol,
        len(positions),
        long_count,
        short_count,
        total_volume,
    )
    all_success, last_result = _execute_closes(resolved, symbol, positions)

    return OrderResult(
        account=resolved.account_key,
        success=all_success,
        dry_run=False,
        strategy=alert.strategy,
        symbol=symbol,
        action=alert.order_id.value,
        volume=total_volume,
        price=last_result.price if last_result else None,
        order_ticket=last_result.order if last_result else None,
        message=last_result.comment if last_result else "No positions closed",
    )


def _execute_closes(resolved: ResolvedAccountConfig, symbol: str, positions: list):
    """Sends close orders for the given open positions (assumes dry_run
    has already been handled by the caller). Returns (all_success,
    last_result) — last_result is None only if `positions` is empty."""
    symbol_info = mt5_client.get_symbol_info(symbol)
    filling_mode = mt5_client.resolve_filling_mode(symbol_info)

    last_result = None
    all_success = True

    for position in positions:
        build_request = partial(
            _build_close_request, position, resolved, symbol, filling_mode
        )
        result = mt5_client.send_order_with_retry(build_request, symbol, resolved.deviation)
        success = result.retcode == mt5_client.TRADE_RETCODE_DONE
        all_success = all_success and success
        last_result = result
        if not success:
            logger.error(
                "Close failed for ticket=%s: retcode=%s comment=%s",
                position.ticket,
                result.retcode,
                result.comment,
            )

    return all_success, last_result


def _close_opposite_positions(
    resolved: ResolvedAccountConfig, symbol: str, opposite_position_type, dry_run: bool
) -> str:
    """Closes any open position(s) on the opposite side before opening a
    new one, so openLong/openShort reverses an existing opposite position
    instead of leaving both a LONG and a SHORT open at once.

    Returns a short note describing what happened (empty string if there
    was nothing to reverse), meant to be prefixed onto the open order's
    result message.
    """
    positions = mt5_client.get_open_positions(symbol, resolved.magic)
    positions = [p for p in positions if p.type == opposite_position_type]
    if not positions:
        return ""

    side = _side_name(opposite_position_type)
    total_volume = sum(p.volume for p in positions)

    if dry_run:
        logger.info(
            "[DRY RUN] Reversing: would close %d %s position(s) volume=%s for symbol=%s before opening opposite side",
            len(positions),
            side,
            total_volume,
            symbol,
        )
        return f"Reversed {len(positions)} {side} position(s) (dry run)"

    logger.info(
        "Reversing: closing %d %s position(s) volume=%s for symbol=%s before opening opposite side",
        len(positions),
        side,
        total_volume,
        symbol,
    )
    all_success, _ = _execute_closes(resolved, symbol, positions)
    status = "closed" if all_success else "failed to fully close"
    return f"Reversed: {status} {len(positions)} {side} position(s)"


def _open_position(
    alert: TradingViewAlert,
    resolved: ResolvedAccountConfig,
    symbol: str,
    order_type,
    dry_run: bool,
    reversal_note: str = "",
) -> OrderResult:
    symbol_info = mt5_client.get_symbol_info(symbol)
    mt5_client.ensure_symbol_tradable(symbol_info)
    filling_mode = mt5_client.resolve_filling_mode(symbol_info)

    tick = mt5_client.get_tick(symbol)
    market_price = tick.ask if order_type == mt5_client.ORDER_TYPE_BUY else tick.bid

    investment = _compute_investment(resolved, alert.order_ratio)
    raw_volume = investment / market_price
    volume = mt5_client.normalize_volume(symbol_info, raw_volume)

    if dry_run:
        logger.info(
            "[DRY RUN] strategy=%s symbol=%s action=%s investment=%s market_price=%s volume=%s",
            alert.strategy,
            symbol,
            alert.order_id.value,
            investment,
            market_price,
            volume,
        )
        message = "Dry run: no order sent to MT5"
        if reversal_note:
            message = f"{reversal_note}. {message}"
        return OrderResult(
            account=resolved.account_key,
            success=True,
            dry_run=True,
            strategy=alert.strategy,
            symbol=symbol,
            action=alert.order_id.value,
            volume=volume,
            price=market_price,
            message=message,
        )

    logger.info(
        "Sending open order: strategy=%s symbol=%s type=%s volume=%s filling_mode=%s",
        alert.strategy,
        symbol,
        order_type,
        volume,
        filling_mode,
    )
    build_request = partial(
        _build_open_request, order_type, volume, resolved, symbol, filling_mode
    )
    result = mt5_client.send_order_with_retry(build_request, symbol, resolved.deviation)

    success = result.retcode == mt5_client.TRADE_RETCODE_DONE
    if not success:
        logger.error(
            "Order failed: retcode=%s comment=%s", result.retcode, result.comment
        )

    message = result.comment
    if reversal_note:
        message = f"{reversal_note}. {message}"

    return OrderResult(
        account=resolved.account_key,
        success=success,
        dry_run=False,
        strategy=alert.strategy,
        symbol=symbol,
        action=alert.order_id.value,
        volume=volume,
        price=result.price if success else None,
        order_ticket=result.order if success else None,
        message=message,
    )


def _close_positions(
    alert: TradingViewAlert, resolved: ResolvedAccountConfig, symbol: str, position_type, dry_run: bool
) -> OrderResult:
    positions = mt5_client.get_open_positions(symbol, resolved.magic)
    positions = [p for p in positions if p.type == position_type]

    if not positions:
        message = (
            f"No open {_side_name(position_type)} "
            f"position found for symbol={symbol} magic={resolved.magic}"
        )
        logger.warning(message)
        return OrderResult(
            account=resolved.account_key,
            success=False,
            dry_run=dry_run,
            strategy=alert.strategy,
            symbol=symbol,
            action=alert.order_id.value,
            message=message,
        )

    total_volume = sum(p.volume for p in positions)

    if dry_run:
        logger.info(
            "[DRY RUN] strategy=%s symbol=%s action=%s would close %d position(s) volume=%s",
            alert.strategy,
            symbol,
            alert.order_id.value,
            len(positions),
            total_volume,
        )
        return OrderResult(
            account=resolved.account_key,
            success=True,
            dry_run=True,
            strategy=alert.strategy,
            symbol=symbol,
            action=alert.order_id.value,
            volume=total_volume,
            message=f"Dry run: would close {len(positions)} position(s), no order sent to MT5",
        )

    logger.info(
        "Closing position(s): strategy=%s symbol=%s count=%d volume=%s",
        alert.strategy,
        symbol,
        len(positions),
        total_volume,
    )
    all_success, last_result = _execute_closes(resolved, symbol, positions)

    return OrderResult(
        account=resolved.account_key,
        success=all_success,
        dry_run=False,
        strategy=alert.strategy,
        symbol=symbol,
        action=alert.order_id.value,
        volume=total_volume,
        price=last_result.price if last_result else None,
        order_ticket=last_result.order if last_result else None,
        message=last_result.comment if last_result else "No positions closed",
    )
