import json

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


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_webhook_accepts_raw_text_plain(tmp_config, monkeypatch):
    def fake_batch(alert, targets):
        return [
            AccountOrderResult(
                account="default",
                success=True,
                dry_run=True,
                strategy=alert.strategy,
                symbol=alert.symbol,
                action=alert.order_id.value,
                volume=1.0,
                price=alert.price,
                message="Dry run",
            )
        ]

    monkeypatch.setattr("app.main.execute_order_batch", fake_batch)

    with TestClient(app) as scoped_client:
        resp = scoped_client.post(
            "/api/order",
            content=_tv_body(),
            headers={"Content-Type": "text/plain"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "eth_strategy_01"
    assert body["results"][0]["success"] is True
    assert body["results"][0]["account"] == "default"
    assert body["results"][0]["symbol"] == "ETHUSDT.P"


def test_webhook_rejects_malformed_json(tmp_config):
    resp = client.post(
        "/api/order",
        content="not-json-at-all{{",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_json"


def test_webhook_rejects_invalid_payload_schema(tmp_config):
    resp = client.post(
        "/api/order",
        content=_tv_body(order_id="notAValidAction"),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"


def test_webhook_rejects_unknown_strategy(tmp_config):
    resp = client.post(
        "/api/order",
        content=_tv_body(strategy="does_not_exist"),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unknown_strategy"


def test_webhook_rejects_unknown_account(tmp_config):
    resp = client.post(
        "/api/order",
        content=_tv_body(account="missing_acc"),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unknown_account"


def test_webhook_rejects_account_disabled(tmp_config, monkeypatch):
    import json as json_mod
    from pathlib import Path

    from app import config as config_module

    path = Path(tmp_config)
    data = json_mod.loads(path.read_text(encoding="utf-8"))
    data["strategies"]["eth_strategy_01"]["accounts"]["default"]["enabled"] = False
    path.write_text(json_mod.dumps(data), encoding="utf-8")
    config_module.get_config.cache_clear()

    resp = client.post(
        "/api/order",
        content=_tv_body(account="default"),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "account_disabled"


def test_webhook_rejects_no_enabled_accounts(tmp_config, monkeypatch):
    import json as json_mod
    from pathlib import Path

    from app import config as config_module

    path = Path(tmp_config)
    data = json_mod.loads(path.read_text(encoding="utf-8"))
    data["strategies"]["eth_strategy_01"]["accounts"]["default"]["enabled"] = False
    path.write_text(json_mod.dumps(data), encoding="utf-8")
    config_module.get_config.cache_clear()

    resp = client.post(
        "/api/order",
        content=_tv_body(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "no_enabled_accounts"


def test_webhook_ignores_duplicate_alert(tmp_config, monkeypatch):
    call_count = {"n": 0}

    def fake_batch(alert, targets):
        call_count["n"] += 1
        return [
            AccountOrderResult(
                account="default",
                success=True,
                dry_run=True,
                strategy=alert.strategy,
                symbol=alert.symbol,
                action=alert.order_id.value,
                volume=1.0,
                price=alert.price,
                message="Dry run: no order sent to MT5",
            )
        ]

    monkeypatch.setattr("app.main.execute_order_batch", fake_batch)

    body = _tv_body()
    with TestClient(app) as scoped_client:
        first = scoped_client.post(
            "/api/order", content=body, headers={"Content-Type": "text/plain"}
        )
        second = scoped_client.post(
            "/api/order", content=body, headers={"Content-Type": "text/plain"}
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "Duplicate" in second.json()["results"][0]["message"]
    assert second.json()["results"][0]["account"] is None
    assert call_count["n"] == 1


def test_webhook_order_execution_error_returns_200_envelope(tmp_config, monkeypatch):
    from app.order_service import OrderExecutionError

    def boom(alert, targets):
        raise OrderExecutionError("Timed out waiting for MT5 lock")

    monkeypatch.setattr("app.main.execute_order_batch", boom)

    with TestClient(app) as scoped_client:
        resp = scoped_client.post(
            "/api/order",
            content=_tv_body(),
            headers={"Content-Type": "text/plain"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "eth_strategy_01"
    assert body["results"][0]["success"] is False
    assert body["results"][0]["account"] is None
    assert "Timed out" in body["results"][0]["message"]
