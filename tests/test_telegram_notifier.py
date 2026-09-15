from unittest.mock import MagicMock

from app import telegram_notifier
from app.schemas import AccountOrderResult
from app.symbols import strip_perpetual_suffix


def _result(**overrides):
    payload = dict(
        account="acc_a",
        success=True,
        dry_run=False,
        strategy="eth_strategy_01",
        symbol="ETHUSD",
        action="openLong",
        volume=1.0,
        price=4000.0,
        order_ticket=555,
        message="Request executed",
    )
    payload.update(overrides)
    return AccountOrderResult(**payload)


def test_format_number_uses_dot_thousands_separator():
    assert telegram_notifier._format_number(12996.0) == "12.996"
    assert telegram_notifier._format_number(4125.1) == "4.125,10"
    assert telegram_notifier._format_number(998) == "998"


def test_display_symbol_strips_perpetual_suffix():
    assert strip_perpetual_suffix("ETHUSDT.P") == "ETHUSDT"
    assert strip_perpetual_suffix("ETHUSD") == "ETHUSD"


def test_format_message_header_matches_expected_style():
    result = _result(symbol="ETHUSDT.P", action="closeLong", price=12996.0, success=True)
    message = telegram_notifier._format_message(result)
    assert message.startswith("✅ ETHUSDT-closeLong: 12.996")


def test_format_message_includes_account_line():
    result = _result(account="acc_a")
    message = telegram_notifier._format_message(result)
    assert "Account: acc_a" in message


def test_format_message_uses_em_dash_when_account_missing():
    result = _result(account=None)
    message = telegram_notifier._format_message(result)
    assert "Account: —" in message


def test_format_message_omits_message_line_on_success():
    result = _result(success=True, message="Request executed")
    message = telegram_notifier._format_message(result)
    assert "Message:" not in message
    assert "Strategy: eth_strategy_01" in message


def test_format_message_keeps_message_line_on_failure():
    result = _result(success=False, message="No open LONG position found")
    message = telegram_notifier._format_message(result)
    assert "Message: No open LONG position found" in message


def test_skips_when_no_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    post_mock = MagicMock()
    monkeypatch.setattr(telegram_notifier.httpx, "post", post_mock)

    telegram_notifier.notify_order_result(_result())

    post_mock.assert_not_called()


def test_skips_when_no_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    post_mock = MagicMock()
    monkeypatch.setattr(telegram_notifier.httpx, "post", post_mock)

    telegram_notifier.notify_order_result(_result())

    post_mock.assert_not_called()


def test_includes_account_in_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    post_mock = MagicMock(return_value=MagicMock(raise_for_status=MagicMock()))
    monkeypatch.setattr(telegram_notifier.httpx, "post", post_mock)

    telegram_notifier.notify_order_result(_result(account="acc_a"))

    post_mock.assert_called_once()
    url = post_mock.call_args[0][0]
    kwargs = post_mock.call_args[1]
    assert url == "https://api.telegram.org/bottok/sendMessage"
    assert kwargs["json"]["chat_id"] == "-100"
    assert kwargs["timeout"] == 3.0
    assert "Account: acc_a" in kwargs["json"]["text"]
    assert "eth_strategy_01" in kwargs["json"]["text"]
    assert "ETHUSD" in kwargs["json"]["text"]


def test_notification_failure_does_not_raise(monkeypatch):
    import httpx as real_httpx

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")

    def boom(*args, **kwargs):
        raise real_httpx.ConnectError("boom")

    monkeypatch.setattr(telegram_notifier.httpx, "post", boom)

    # Should not raise.
    telegram_notifier.notify_order_result(_result())
