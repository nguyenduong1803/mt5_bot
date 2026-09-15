from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import mt5_client
from app.config import get_mt5_terminal_path, resolve_account_config


def _tick(bid, ask):
    return SimpleNamespace(bid=bid, ask=ask)


def test_ensure_connection_passes_login_password_server_to_initialize(tmp_config, fake_mt5):
    resolved = resolve_account_config("eth_strategy_01", "default")
    fake_mt5.initialize.reset_mock()
    mt5_client._current_terminal = None

    mt5_client.ensure_connection(resolved)

    fake_mt5.initialize.assert_called_once_with(
        path=get_mt5_terminal_path(resolved.mt5),
        login=resolved.mt5.l,
        password=resolved.mt5.p,
        server=resolved.mt5.server,
    )


def test_get_tick_returns_immediately_when_price_is_valid(monkeypatch, fake_mt5):
    fake_mt5.symbol_info_tick = MagicMock(return_value=_tick(1999, 2001))

    tick = mt5_client.get_tick("ETHUSD")

    assert tick.bid == 1999
    assert tick.ask == 2001
    fake_mt5.symbol_info_tick.assert_called_once()


def test_get_tick_waits_out_zero_price_then_returns_valid_tick(monkeypatch, fake_mt5):
    # Right after being added to Market Watch, MT5 can report bid/ask as 0
    # for a moment before the first real quote arrives.
    fake_mt5.symbol_info_tick = MagicMock(
        side_effect=[_tick(0, 0), _tick(0, 0), _tick(1999, 2001)]
    )

    tick = mt5_client.get_tick("ETHUSD")

    assert tick.bid == 1999
    assert fake_mt5.symbol_info_tick.call_count == 3


def test_get_tick_raises_mt5error_if_price_never_becomes_valid(monkeypatch, fake_mt5):
    fake_mt5.symbol_info_tick = MagicMock(return_value=_tick(0, 0))
    # Simulate the deadline passing after a couple of checks without
    # actually sleeping for real seconds in the test.
    monkeypatch.setattr(mt5_client.time, "monotonic", MagicMock(side_effect=[0, 0, 10]))

    with pytest.raises(mt5_client.MT5Error, match="No live price"):
        mt5_client.get_tick("ETHUSD")


def test_get_tick_raises_mt5error_when_tick_is_none(monkeypatch, fake_mt5):
    fake_mt5.symbol_info_tick = MagicMock(return_value=None)
    monkeypatch.setattr(mt5_client.time, "monotonic", MagicMock(side_effect=[0, 0, 10]))

    with pytest.raises(mt5_client.MT5Error, match="Failed to get tick"):
        mt5_client.get_tick("ETHUSD")
