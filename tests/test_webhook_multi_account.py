"""Integration tests for multi-account webhook fan-out and partial failure."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AccountOrderResult

client = TestClient(app)


def _tv_body(**overrides):
    payload = {
        "symbol": "ETHUSDT.P",
        "price": "4123.45",
        "alert_name": "ETH Long",
        "timenow": "2026-08-27T00:00:00Z",
        "order_id": "openLong",
        "order_action": "buy",
        "comment": "eth_strategy_01",
        "alert_message": None,
        "order_ratio": 1,
        "strategy": "eth_strategy_01",
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture()
def multi_account_config(tmp_path, monkeypatch):
    config = {
        "dryRun": True,
        "strategies": {
            "eth_strategy_01": {
                "price": 1000,
                "deviation": 20,
                "comment": "ETH Strategy 01",
                "accounts": {
                    "acc_a": {
                        "magic": 100001,
                        "mt5": {"l": 111, "p": "pass-a", "server": "S1"},
                    },
                    "acc_b": {
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
    yield path
    config_module.get_config.cache_clear()


def _ok_result(alert, account: str) -> AccountOrderResult:
    return AccountOrderResult(
        account=account,
        success=True,
        dry_run=True,
        strategy=alert.strategy,
        symbol=alert.symbol,
        action=alert.order_id.value,
        volume=1.0,
        price=alert.price,
        message="Dry run",
    )


def test_fan_out_returns_results_for_all_enabled_accounts(
    multi_account_config, monkeypatch
):
    calls = []

    def fake_batch(alert, targets):
        calls.append(targets)
        return [_ok_result(alert, t.account_key) for t in targets]

    monkeypatch.setattr("app.main.execute_order_batch", fake_batch)

    with TestClient(app) as scoped_client:
        resp = scoped_client.post(
            "/api/order",
            content=_tv_body(),
            headers={"Content-Type": "text/plain"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 2
    accounts = {r["account"] for r in body["results"]}
    assert accounts == {"acc_a", "acc_b"}
    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert {t.account_key for t in calls[0]} == {"acc_a", "acc_b"}


def test_single_account_filter_calls_batch_with_one_target(
    multi_account_config, monkeypatch
):
    calls = []

    def fake_batch(alert, targets):
        calls.append(targets)
        return [_ok_result(alert, t.account_key) for t in targets]

    monkeypatch.setattr("app.main.execute_order_batch", fake_batch)

    with TestClient(app) as scoped_client:
        resp = scoped_client.post(
            "/api/order",
            content=_tv_body(account="acc_a"),
            headers={"Content-Type": "text/plain"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["account"] == "acc_a"
    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert calls[0][0].account_key == "acc_a"


def test_partial_failure_still_returns_http_200(multi_account_config, monkeypatch):
    def fake_batch(alert, targets):
        return [
            AccountOrderResult(
                account="acc_a",
                success=True,
                dry_run=True,
                strategy=alert.strategy,
                symbol=alert.symbol,
                action=alert.order_id.value,
                volume=1.0,
                price=alert.price,
                message="ok",
            ),
            AccountOrderResult(
                account="acc_b",
                success=False,
                dry_run=True,
                strategy=alert.strategy,
                symbol=alert.symbol,
                action=alert.order_id.value,
                message="broker rejected",
            ),
        ]

    monkeypatch.setattr("app.main.execute_order_batch", fake_batch)

    with TestClient(app) as scoped_client:
        resp = scoped_client.post(
            "/api/order",
            content=_tv_body(),
            headers={"Content-Type": "text/plain"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 2
    by_account = {r["account"]: r for r in results}
    assert by_account["acc_a"]["success"] is True
    assert by_account["acc_b"]["success"] is False
    assert "broker rejected" in by_account["acc_b"]["message"]


def test_telegram_failure_does_not_affect_webhook_response(
    multi_account_config, monkeypatch
):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def fake_batch(alert, targets):
        return [_ok_result(alert, "acc_a")]

    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("telegram unreachable")

    monkeypatch.setattr("app.main.execute_order_batch", fake_batch)
    monkeypatch.setattr("app.telegram_notifier.httpx.post", boom)

    def notify_boom(*_args, **_kwargs):
        raise RuntimeError("notify failed")

    # Ensure a raised notify cannot change the already-built HTTP response.
    monkeypatch.setattr("app.main.notify_order_result", notify_boom)

    with TestClient(app) as scoped_client:
        resp = scoped_client.post(
            "/api/order",
            content=_tv_body(account="acc_a"),
            headers={"Content-Type": "text/plain"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["success"] is True
    assert body["results"][0]["account"] == "acc_a"
