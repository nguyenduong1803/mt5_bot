"""Sends order notifications to Telegram.

Notifications are best-effort: any failure here is logged and swallowed,
never allowed to affect the order execution result already returned to
TradingView.
"""
import httpx

from app.config import get_telegram_bot_token, get_telegram_chat_id
from app.logging_config import logger
from app.schemas import AccountOrderResult
from app.symbols import strip_perpetual_suffix

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _format_number(value: float) -> str:
    """Vietnamese-style number formatting: '.' for thousands, ',' for the
    decimal separator (e.g. 12996.0 -> "12.996", 4125.1 -> "4.125,10")."""
    formatted = f"{value:,.2f}"
    integer_part, _, decimal_part = formatted.partition(".")
    integer_part = integer_part.replace(",", ".")
    if decimal_part == "00":
        return integer_part
    return f"{integer_part},{decimal_part}"


def _format_message(result: AccountOrderResult) -> str:
    if result.dry_run:
        status_emoji = "🧪"
    elif result.success:
        status_emoji = "✅"
    else:
        status_emoji = "❌"

    price_str = _format_number(result.price) if result.price is not None else "—"
    header = f"{status_emoji} {strip_perpetual_suffix(result.symbol)}-{result.action}: {price_str}"

    lines = [
        header,
        f"Strategy: {result.strategy}",
        f"Account: {result.account or '—'}",
    ]
    if not result.success:
        lines.append(f"Message: {result.message}")

    return "\n".join(lines)


def notify_order_result(result: AccountOrderResult) -> None:
    token = get_telegram_bot_token()
    chat_id = get_telegram_chat_id()
    if not token or not chat_id:
        return

    message = _format_message(result)
    url = TELEGRAM_API_URL.format(token=token)

    try:
        response = httpx.post(
            url,
            json={"chat_id": chat_id, "text": message},
            timeout=3.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Failed to send Telegram notification: %s", e)
