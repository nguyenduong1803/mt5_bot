"""Test fixtures.

The real `MetaTrader5` package only runs on Windows against a live
terminal, so we inject a fake module into `sys.modules` before any
`app.*` module imports it. This lets the whole test suite run on any
platform without a real MT5 installation.
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _install_fake_mt5():
    if "MetaTrader5" in sys.modules and getattr(
        sys.modules["MetaTrader5"], "_is_fake", False
    ):
        return sys.modules["MetaTrader5"]

    fake = types.ModuleType("MetaTrader5")
    fake._is_fake = True

    fake.ORDER_TYPE_BUY = 0
    fake.ORDER_TYPE_SELL = 1
    fake.TRADE_ACTION_DEAL = 1
    fake.ORDER_TIME_GTC = 0
    fake.ORDER_FILLING_FOK = 0
    fake.ORDER_FILLING_IOC = 1
    fake.ORDER_FILLING_RETURN = 2
    fake.TRADE_RETCODE_DONE = 10009
    fake.TRADE_RETCODE_REQUOTE = 10004
    fake.TRADE_RETCODE_PRICE_CHANGED = 10020
    fake.SYMBOL_TRADE_MODE_DISABLED = 0
    fake.SYMBOL_TRADE_MODE_FULL = 4
    fake.POSITION_TYPE_BUY = 0
    fake.POSITION_TYPE_SELL = 1

    fake.initialize = MagicMock(return_value=True)
    fake.shutdown = MagicMock()
    fake.last_error = MagicMock(return_value=(0, "no error"))
    fake.terminal_info = MagicMock(return_value=MagicMock())
    fake.symbol_info = MagicMock()
    fake.symbol_select = MagicMock(return_value=True)
    fake.symbol_info_tick = MagicMock()
    fake.positions_get = MagicMock(return_value=[])
    fake.order_send = MagicMock()

    # Account info mock with all required attributes
    _account_info_mock = MagicMock()
    _account_info_mock.balance = 100000.0
    _account_info_mock.equity = 100000.0
    _account_info_mock.margin = 0.0
    _account_info_mock.free_margin = 100000.0
    _account_info_mock.profit = 0.0
    _account_info_mock.leverage = 100
    _account_info_mock.margin_level = 0.0
    _account_info_mock.credit = 0.0
    fake.account_info = MagicMock(return_value=_account_info_mock)

    sys.modules["MetaTrader5"] = fake
    return fake


_install_fake_mt5()


@pytest.fixture()
def fake_mt5():
    return _install_fake_mt5()


@pytest.fixture(autouse=True)
def _clear_dedupe_cache():
    from app import dedupe

    dedupe._seen.clear()
    yield
    dedupe._seen.clear()


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    from app import mt5_client

    monkeypatch.setattr(mt5_client.time, "sleep", lambda *_: None)


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    config = {
        "dryRun": True,
        "strategies": {
            "eth_strategy_01": {
                "price": 1000,
                "deviation": 20,
                "magic": 100001,
                "comment": "ETH Strategy 01",
                "mt5": {
                    "login": 12345678,
                    "server": "TestServer",
                },
            }
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("MT5_PASSWORD_ETH_STRATEGY_01", "test-password")
    monkeypatch.delenv("DRY_RUN", raising=False)

    from app import config as config_module

    config_module.get_config.cache_clear()
    yield config_path
    config_module.get_config.cache_clear()
