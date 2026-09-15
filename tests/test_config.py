import json

import pytest

from app.config import (
    ConfigError,
    get_config,
    resolve_account_config,
    resolve_execution_targets,
)


@pytest.fixture
def nested_config(tmp_path, monkeypatch):
    config = {
        "dryRun": True,
        "strategies": {
            "eth_strategy_01": {
                "price": 1000,
                "deviation": 20,
                "comment": "default",
                "accounts": {
                    "acc_a": {
                        "magic": 100001,
                        "price": 1500,
                        "mt5": {"l": 111, "p": "pass-a", "server": "S1"},
                    },
                    "acc_b": {
                        "enabled": False,
                        "magic": 100002,
                        "mt5": {"l": 222, "p": "pass-b", "server": "S2"},
                    },
                },
            }
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    from app import config as config_module

    config_module.get_config.cache_clear()
    yield
    config_module.get_config.cache_clear()


@pytest.fixture
def all_disabled_config(tmp_path, monkeypatch):
    config = {
        "dryRun": True,
        "strategies": {
            "eth_strategy_01": {
                "price": 1000,
                "deviation": 20,
                "comment": "default",
                "accounts": {
                    "acc_a": {
                        "enabled": False,
                        "magic": 100001,
                        "mt5": {"l": 111, "p": "pass-a", "server": "S1"},
                    },
                    "acc_b": {
                        "enabled": False,
                        "magic": 100002,
                        "mt5": {"l": 222, "p": "pass-b", "server": "S2"},
                    },
                },
            }
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    from app import config as config_module

    config_module.get_config.cache_clear()
    yield
    config_module.get_config.cache_clear()


def test_resolve_account_merges_strategy_defaults(nested_config):
    resolved = resolve_account_config("eth_strategy_01", "acc_a")
    assert resolved.price == 1500
    assert resolved.deviation == 20
    assert resolved.comment == "default"
    assert resolved.magic == 100001
    assert resolved.mt5.l == 111
    assert resolved.mt5.p == "pass-a"
    assert resolved.account_key == "acc_a"
    assert resolved.strategy_name == "eth_strategy_01"


def test_fan_out_enabled_accounts_only(nested_config):
    targets = resolve_execution_targets("eth_strategy_01", account=None)
    assert len(targets) == 1
    assert targets[0].account_key == "acc_a"


def test_single_account_target(nested_config):
    targets = resolve_execution_targets("eth_strategy_01", account="acc_a")
    assert len(targets) == 1
    assert targets[0].account_key == "acc_a"


def test_unknown_account_raises(nested_config):
    with pytest.raises(ConfigError, match="unknown_account"):
        resolve_execution_targets("eth_strategy_01", account="nope")


def test_disabled_account_raises(nested_config):
    with pytest.raises(ConfigError, match="account_disabled"):
        resolve_execution_targets("eth_strategy_01", account="acc_b")


def test_no_enabled_accounts_raises(all_disabled_config):
    with pytest.raises(ConfigError, match="no_enabled_accounts"):
        resolve_execution_targets("eth_strategy_01", account=None)


def test_nested_config_loads(nested_config):
    config = get_config()
    strategy = config.strategies["eth_strategy_01"]
    assert "acc_a" in strategy.accounts
    assert strategy.accounts["acc_a"].mt5.l == 111
