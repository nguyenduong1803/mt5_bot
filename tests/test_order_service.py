from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import mt5_client, order_service
from app.config import resolve_account_config
from app.schemas import AccountOrderResult, TradingViewAlert


def _alert(**overrides):
    payload = {
        "symbol": "ETHUSD",
        "price": 4000.0,
        "order_id": "openLong",
        "order_ratio": 2,
        "strategy": "eth_strategy_01",
    }
    payload.update(overrides)
    return TradingViewAlert.model_validate(payload)


def _symbol_info(**overrides):
    payload = dict(
        name="ETHUSD",
        volume_step=0.01,
        volume_min=0.01,
        volume_max=100.0,
        trade_mode=mt5_client.mt5.SYMBOL_TRADE_MODE_FULL,
        filling_mode=2,  # IOC bit set
    )
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _mock_symbol_and_tick(monkeypatch, ask=4001, bid=3999, symbol_info=None):
    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(
        mt5_client,
        "get_symbol_info",
        MagicMock(return_value=symbol_info or _symbol_info()),
    )
    monkeypatch.setattr(
        mt5_client,
        "get_tick",
        MagicMock(return_value=SimpleNamespace(ask=ask, bid=bid)),
    )


def test_execute_order_fails_fast_if_lock_held_too_long(tmp_config, monkeypatch):
    """If another call is stuck holding MT5_LOCK (e.g. MT5 terminal frozen),
    new calls must fail with a clear error after LOCK_TIMEOUT_SECONDS
    instead of hanging forever waiting for it."""
    import threading

    monkeypatch.setattr(order_service, "LOCK_TIMEOUT_SECONDS", 0.2)
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong")

    # RLock is reentrant per-thread, so the lock must be held by a *different*
    # thread for acquiring it here to actually block/time out.
    acquired_event = threading.Event()
    release_event = threading.Event()

    def hold_lock():
        mt5_client.MT5_LOCK.acquire()
        acquired_event.set()
        release_event.wait(2)
        mt5_client.MT5_LOCK.release()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    acquired_event.wait(2)
    try:
        with pytest.raises(order_service.OrderExecutionError, match="Timed out"):
            order_service.execute_order_for_account(alert, strategy)
    finally:
        release_event.set()
        holder.join()


def test_compute_investment():
    strategy = SimpleNamespace(price=2000)
    # investment = strategy.price * order_ratio
    assert order_service._compute_investment(strategy, order_ratio=1.5) == pytest.approx(3000)


def test_normalize_volume_rounds_to_step():
    symbol_info = _symbol_info(volume_step=0.01, volume_min=0.01, volume_max=100.0)
    assert mt5_client.normalize_volume(symbol_info, 1.2345) == pytest.approx(1.23)


def test_normalize_volume_clamps_to_min_max():
    symbol_info = _symbol_info(volume_step=0.01, volume_min=0.1, volume_max=1.0)
    assert mt5_client.normalize_volume(symbol_info, 0.001) == pytest.approx(0.1)
    assert mt5_client.normalize_volume(symbol_info, 5) == pytest.approx(1.0)


def test_normalize_volume_handles_non_power_of_ten_step_precisely():
    # A naive float round() on a 0.05 step can drift (e.g. round(0.15, 1)
    # misrounds to 0.1 due to binary float representation). Decimal-based
    # rounding must land exactly on the step grid.
    symbol_info = _symbol_info(volume_step=0.05, volume_min=0.05, volume_max=100.0)
    assert mt5_client.normalize_volume(symbol_info, 0.17) == pytest.approx(0.15)
    assert mt5_client.normalize_volume(symbol_info, 0.12) == pytest.approx(0.10)


def test_resolve_filling_mode_prefers_ioc():
    symbol_info = _symbol_info(filling_mode=1 | 2)  # FOK + IOC supported
    assert mt5_client.resolve_filling_mode(symbol_info) == mt5_client.ORDER_FILLING_IOC


def test_resolve_filling_mode_falls_back_to_fok():
    symbol_info = _symbol_info(filling_mode=1)  # only FOK supported
    assert mt5_client.resolve_filling_mode(symbol_info) == mt5_client.ORDER_FILLING_FOK


def test_resolve_filling_mode_falls_back_to_return():
    symbol_info = _symbol_info(filling_mode=0)  # neither bit set
    assert mt5_client.resolve_filling_mode(symbol_info) == mt5_client.ORDER_FILLING_RETURN


def test_ensure_symbol_tradable_raises_when_disabled():
    symbol_info = _symbol_info(trade_mode=mt5_client.mt5.SYMBOL_TRADE_MODE_DISABLED)
    with pytest.raises(mt5_client.MT5Error):
        mt5_client.ensure_symbol_tradable(symbol_info)


def test_execute_order_dry_run_still_connects_and_uses_real_tick(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    strategy = resolve_account_config("eth_strategy_01", "default")  # price=1000 -> $1000 base investment
    alert = _alert(order_id="openLong", order_ratio=1)

    ensure_connection_mock = MagicMock()
    monkeypatch.setattr(mt5_client, "ensure_connection", ensure_connection_mock)
    monkeypatch.setattr(
        mt5_client, "get_symbol_info", MagicMock(return_value=_symbol_info())
    )
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=1000, bid=998))
    )
    send_order_mock = MagicMock()
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.dry_run is True
    assert result.success is True
    # investment = 1000 * 1 = 1000; volume = 1000 / ask(1000) = 1.0
    assert result.volume == pytest.approx(1.0)
    assert result.price == 1000
    ensure_connection_mock.assert_called_once()
    send_order_mock.assert_not_called()


def test_execute_order_open_long_sends_buy_order(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1)

    _mock_symbol_and_tick(monkeypatch, ask=1000, bid=998)
    send_order_mock = MagicMock(
        return_value=SimpleNamespace(
            retcode=mt5_client.TRADE_RETCODE_DONE,
            price=1000,
            order=555,
            comment="Request executed",
        )
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is True
    assert result.order_ticket == 555
    assert result.symbol == "ETHUSD"
    sent_request = send_order_mock.call_args[0][0]
    assert sent_request["type"] == mt5_client.ORDER_TYPE_BUY
    assert sent_request["symbol"] == "ETHUSD"
    assert sent_request["type_filling"] == mt5_client.ORDER_FILLING_IOC
    assert "sl" not in sent_request
    assert "tp" not in sent_request
    # investment = 1000 * 1 = 1000; volume = 1000 / ask(1000) = 1.0
    assert result.volume == pytest.approx(1.0)


def test_execute_order_open_long_uses_fok_when_ioc_unsupported(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1)

    _mock_symbol_and_tick(monkeypatch, ask=1000, bid=998, symbol_info=_symbol_info(filling_mode=1))
    send_order_mock = MagicMock(
        return_value=SimpleNamespace(
            retcode=mt5_client.TRADE_RETCODE_DONE, price=1000, order=1, comment="ok"
        )
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    order_service.execute_order_for_account(alert, strategy)

    sent_request = send_order_mock.call_args[0][0]
    assert sent_request["type_filling"] == mt5_client.ORDER_FILLING_FOK


def test_execute_order_open_long_rejects_disabled_symbol(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1)

    _mock_symbol_and_tick(
        monkeypatch,
        symbol_info=_symbol_info(trade_mode=mt5_client.mt5.SYMBOL_TRADE_MODE_DISABLED),
    )
    send_order_mock = MagicMock()
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    with pytest.raises(order_service.OrderExecutionError):
        order_service.execute_order_for_account(alert, strategy)

    send_order_mock.assert_not_called()


def test_execute_order_retries_on_requote_with_fresh_price(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1)

    _mock_symbol_and_tick(monkeypatch, ask=1000, bid=998)
    send_order_mock = MagicMock(
        side_effect=[
            SimpleNamespace(
                retcode=mt5_client.TRADE_RETCODE_REQUOTE, price=None, order=None, comment="Requote"
            ),
            SimpleNamespace(
                retcode=mt5_client.TRADE_RETCODE_DONE, price=1001, order=42, comment="Request executed"
            ),
        ]
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is True
    assert result.order_ticket == 42
    assert send_order_mock.call_count == 2
    # deviation widens 3x on retry so a real price spike still gets a fair
    # chance to fill, instead of retrying at the same (rejected) tolerance.
    first_deviation = send_order_mock.call_args_list[0][0][0]["deviation"]
    second_deviation = send_order_mock.call_args_list[1][0][0]["deviation"]
    assert first_deviation == strategy.deviation
    assert second_deviation == strategy.deviation * 3


def test_send_order_with_retry_gives_up_after_max_attempts_and_caps_deviation(monkeypatch):
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=1000, bid=998))
    )
    requote = SimpleNamespace(
        retcode=mt5_client.TRADE_RETCODE_REQUOTE, price=None, order=None, comment="Requote"
    )
    send_order_mock = MagicMock(return_value=requote)
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    seen_deviations = []

    def build_request(tick, deviation):
        seen_deviations.append(deviation)
        return {"deviation": deviation}

    result = mt5_client.send_order_with_retry(
        build_request, "ETHUSD", base_deviation=200, max_attempts=5, deviation_growth=3
    )

    # Never actually fills (always requoted) -> gives up, returns the last
    # rejection instead of retrying forever or raising.
    assert result is requote
    assert send_order_mock.call_count == 5
    # 200 -> 600 -> 1800 -> capped at 25x base (5000) -> stays at 5000
    assert seen_deviations == [200, 600, 1800, 5000, 5000]


def test_send_order_with_retry_does_not_retry_non_price_rejections(monkeypatch):
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=1000, bid=998))
    )
    invalid_volume = SimpleNamespace(
        retcode=10014, price=None, order=None, comment="Invalid volume"
    )
    send_order_mock = MagicMock(return_value=invalid_volume)
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = mt5_client.send_order_with_retry(
        lambda tick, deviation: {"deviation": deviation}, "ETHUSD", base_deviation=200
    )

    assert result is invalid_volume
    send_order_mock.assert_called_once()


def test_execute_order_uses_alert_symbol_not_config(tmp_config, monkeypatch):
    """Symbol to trade must come from the webhook payload, not config.json."""
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1, symbol="XAUUSD")

    _mock_symbol_and_tick(
        monkeypatch, ask=2000, bid=1998, symbol_info=_symbol_info(name="XAUUSD")
    )
    send_order_mock = MagicMock(
        return_value=SimpleNamespace(
            retcode=mt5_client.TRADE_RETCODE_DONE, price=2000, order=1, comment="ok"
        )
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.symbol == "XAUUSD"
    sent_request = send_order_mock.call_args[0][0]
    assert sent_request["symbol"] == "XAUUSD"


def test_execute_order_strips_dot_p_suffix_before_trading(tmp_config, monkeypatch):
    """TradingView's perpetual-futures suffix (e.g. "BTCUSDT.P") never
    matches a real MT5 symbol name, so it must be stripped before any MT5
    call — not just when formatting the Telegram notification."""
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1, symbol="BTCUSDT.P")

    get_symbol_info_mock = MagicMock(return_value=_symbol_info(name="BTCUSDT"))
    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(mt5_client, "get_symbol_info", get_symbol_info_mock)
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=2000, bid=1998))
    )
    send_order_mock = MagicMock(
        return_value=SimpleNamespace(
            retcode=mt5_client.TRADE_RETCODE_DONE, price=2000, order=1, comment="ok"
        )
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.symbol == "BTCUSDT"
    get_symbol_info_mock.assert_called_once_with("BTCUSDT")
    sent_request = send_order_mock.call_args[0][0]
    assert sent_request["symbol"] == "BTCUSDT"


def test_execute_order_open_long_reverses_existing_short_position(tmp_config, monkeypatch):
    """If a SHORT position is open when openLong comes in, the bot must
    close the SHORT first, then open the LONG — never leave both sides
    open at once."""
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1)

    short_position = SimpleNamespace(
        ticket=111, volume=0.3, type=mt5_client.POSITION_TYPE_SELL, magic=strategy.magic
    )
    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(
        mt5_client, "get_open_positions", MagicMock(return_value=[short_position])
    )
    monkeypatch.setattr(
        mt5_client, "get_symbol_info", MagicMock(return_value=_symbol_info())
    )
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=1000, bid=998))
    )
    send_order_mock = MagicMock(
        side_effect=[
            # 1st call: closing the existing SHORT position (reversal step)
            SimpleNamespace(
                retcode=mt5_client.TRADE_RETCODE_DONE, price=998, order=222, comment="Closed"
            ),
            # 2nd call: opening the new LONG position
            SimpleNamespace(
                retcode=mt5_client.TRADE_RETCODE_DONE, price=1000, order=333, comment="Opened"
            ),
        ]
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is True
    assert result.order_ticket == 333  # the new LONG order, not the close
    assert "Reversed" in result.message
    assert send_order_mock.call_count == 2

    close_request = send_order_mock.call_args_list[0][0][0]
    assert close_request["type"] == mt5_client.ORDER_TYPE_BUY  # closes a SHORT via BUY
    assert close_request["position"] == 111

    open_request = send_order_mock.call_args_list[1][0][0]
    assert open_request["type"] == mt5_client.ORDER_TYPE_BUY
    assert "position" not in open_request


def test_execute_order_open_short_reverses_existing_long_position(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openShort", order_ratio=1)

    long_position = SimpleNamespace(
        ticket=444, volume=0.2, type=mt5_client.POSITION_TYPE_BUY, magic=strategy.magic
    )
    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(
        mt5_client, "get_open_positions", MagicMock(return_value=[long_position])
    )
    monkeypatch.setattr(
        mt5_client, "get_symbol_info", MagicMock(return_value=_symbol_info())
    )
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=1000, bid=998))
    )
    send_order_mock = MagicMock(
        side_effect=[
            SimpleNamespace(
                retcode=mt5_client.TRADE_RETCODE_DONE, price=1000, order=555, comment="Closed"
            ),
            SimpleNamespace(
                retcode=mt5_client.TRADE_RETCODE_DONE, price=998, order=666, comment="Opened"
            ),
        ]
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is True
    assert result.order_ticket == 666
    assert "Reversed" in result.message

    close_request = send_order_mock.call_args_list[0][0][0]
    assert close_request["type"] == mt5_client.ORDER_TYPE_SELL  # closes a LONG via SELL
    assert close_request["position"] == 444

    open_request = send_order_mock.call_args_list[1][0][0]
    assert open_request["type"] == mt5_client.ORDER_TYPE_SELL


def test_execute_order_open_long_no_reversal_when_no_opposite_position(tmp_config, monkeypatch):
    """Normal case (no existing opposite position): no reversal note, no
    extra close order sent."""
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1)

    _mock_symbol_and_tick(monkeypatch, ask=1000, bid=998)
    monkeypatch.setattr(mt5_client, "get_open_positions", MagicMock(return_value=[]))
    send_order_mock = MagicMock(
        return_value=SimpleNamespace(
            retcode=mt5_client.TRADE_RETCODE_DONE, price=1000, order=1, comment="Request executed"
        )
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is True
    assert "Reversed" not in result.message
    send_order_mock.assert_called_once()


def test_execute_order_open_long_reversal_dry_run_does_not_send(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="openLong", order_ratio=1)

    short_position = SimpleNamespace(
        ticket=111, volume=0.3, type=mt5_client.POSITION_TYPE_SELL, magic=strategy.magic
    )
    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(
        mt5_client, "get_open_positions", MagicMock(return_value=[short_position])
    )
    monkeypatch.setattr(
        mt5_client, "get_symbol_info", MagicMock(return_value=_symbol_info())
    )
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=1000, bid=998))
    )
    send_order_mock = MagicMock()
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.dry_run is True
    assert result.success is True
    assert "Reversed" in result.message
    send_order_mock.assert_not_called()


def test_execute_order_close_long_no_position_returns_failure(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="closeLong")

    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(mt5_client, "get_open_positions", MagicMock(return_value=[]))
    send_order_mock = MagicMock()
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is False
    send_order_mock.assert_not_called()


def test_execute_order_close_long_closes_matching_position(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="closeLong")

    position = SimpleNamespace(
        ticket=999, volume=0.5, type=mt5_client.POSITION_TYPE_BUY, magic=strategy.magic
    )
    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(
        mt5_client, "get_open_positions", MagicMock(return_value=[position])
    )
    monkeypatch.setattr(
        mt5_client, "get_symbol_info", MagicMock(return_value=_symbol_info())
    )
    monkeypatch.setattr(
        mt5_client, "get_tick", MagicMock(return_value=SimpleNamespace(ask=4001, bid=3999))
    )
    send_order_mock = MagicMock(
        return_value=SimpleNamespace(
            retcode=mt5_client.TRADE_RETCODE_DONE,
            price=3999,
            order=777,
            comment="Request executed",
        )
    )
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is True
    sent_request = send_order_mock.call_args[0][0]
    assert sent_request["type"] == mt5_client.ORDER_TYPE_SELL
    assert sent_request["position"] == 999
    assert sent_request["volume"] == 0.5


def test_execute_order_close_long_dry_run_does_not_send(tmp_config, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    strategy = resolve_account_config("eth_strategy_01", "default")
    alert = _alert(order_id="closeLong")

    position = SimpleNamespace(
        ticket=999, volume=0.5, type=mt5_client.POSITION_TYPE_BUY, magic=strategy.magic
    )
    monkeypatch.setattr(mt5_client, "ensure_connection", MagicMock())
    monkeypatch.setattr(
        mt5_client, "get_open_positions", MagicMock(return_value=[position])
    )
    send_order_mock = MagicMock()
    monkeypatch.setattr(mt5_client, "send_order", send_order_mock)

    result = order_service.execute_order_for_account(alert, strategy)

    assert result.success is True
    assert result.dry_run is True
    assert result.volume == pytest.approx(0.5)
    send_order_mock.assert_not_called()


def test_execute_order_batch_isolates_failures_and_releases_lock(tmp_config, monkeypatch):
    """Batch holds one lock for all accounts; a failure on one must not
    stop the rest, and the lock must be released afterward."""
    first = resolve_account_config("eth_strategy_01", "default")
    second = first.model_copy(update={"account_key": "acc_b"})
    alert = _alert(order_id="openLong", order_ratio=1)

    def fake_execute(alert, resolved, _lock_held=False):
        assert _lock_held is True
        if resolved.account_key == "acc_b":
            raise order_service.OrderExecutionError("second account failed")
        return AccountOrderResult(
            account=resolved.account_key,
            success=True,
            dry_run=True,
            strategy=alert.strategy,
            symbol=alert.symbol,
            action=alert.order_id.value,
            message="ok",
        )

    monkeypatch.setattr(order_service, "execute_order_for_account", fake_execute)

    results = order_service.execute_order_batch(alert, [first, second])

    assert len(results) == 2
    assert results[0].success is True
    assert results[0].account == "default"
    assert results[1].success is False
    assert results[1].account == "acc_b"
    assert "second account failed" in results[1].message
    # Lock released so another thread can acquire it.
    assert mt5_client.MT5_LOCK.acquire(blocking=False) is True
    mt5_client.MT5_LOCK.release()
