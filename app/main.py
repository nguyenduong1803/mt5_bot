import asyncio
import json
from typing import List

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import (
    ConfigError,
    is_app_dry_run,
    is_dry_run,
    resolve_account_config,
    resolve_execution_targets,
)
from app.dedupe import is_duplicate
from app.logging_config import logger
from app.order_service import OrderExecutionError, execute_order_batch
from app.schemas import AccountOrderResult, OrderBatchResponse, TradingViewAlert
from app.telegram_notifier import notify_order_result

app = FastAPI(
    title="MT5 TradingView Webhook Bot",
    description="Receives TradingView webhook alerts and executes orders on MetaTrader 5 (Exness, FTMO)",
    version="1.0.0",
)


def _config_error_code(message: str) -> str:
    if "unknown_account" in message:
        return "unknown_account"
    if "account_disabled" in message:
        return "account_disabled"
    if "no_enabled_accounts" in message:
        return "no_enabled_accounts"
    if "Unknown strategy" in message or "unknown_strategy" in message:
        return "unknown_strategy"
    return "config_error"


async def _notify_batch_async(results: List[AccountOrderResult]) -> None:
    loop = asyncio.get_running_loop()
    for r in results:
        await loop.run_in_executor(None, notify_order_result, r)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/order", response_model=OrderBatchResponse)
async def receive_order(request: Request):
    raw_body = await request.body()
    raw_text = raw_body.decode("utf-8", errors="replace")
    logger.info("Received webhook payload: %s", raw_text)

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse JSON body: %s", e)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_json", "detail": str(e)},
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_payload", "detail": "JSON body must be an object"},
        )

    try:
        alert = TradingViewAlert.model_validate(payload)
    except ValidationError as e:
        logger.warning("Payload validation failed: %s", e)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "validation_error", "detail": json.loads(e.json())},
        )

    try:
        targets = resolve_execution_targets(alert.strategy, alert.account)
    except ConfigError as e:
        error_code = _config_error_code(str(e))
        logger.error("Config error resolving targets for strategy=%s: %s", alert.strategy, e)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": error_code, "detail": str(e)},
        )

    if alert.account is not None:
        dry_run = is_dry_run(resolve_account_config(alert.strategy, alert.account))
    else:
        dry_run = is_app_dry_run()

    logger.info(
        "Processing order: strategy=%s account=%s symbol=%s order_id=%s dry_run=%s targets=%s",
        alert.strategy,
        alert.account,
        alert.symbol,
        alert.order_id.value,
        dry_run,
        [t.account_key for t in targets],
    )

    if is_duplicate(alert):
        logger.warning(
            "Duplicate alert ignored (same strategy/order_id/symbol/comment/"
            "timenow seen recently): strategy=%s order_id=%s",
            alert.strategy,
            alert.order_id.value,
        )
        batch = OrderBatchResponse(
            strategy=alert.strategy,
            results=[
                AccountOrderResult(
                    account=alert.account,
                    success=True,
                    dry_run=dry_run,
                    strategy=alert.strategy,
                    symbol=alert.symbol,
                    action=alert.order_id.value,
                    message="Duplicate alert ignored (already processed this signal recently)",
                )
            ],
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=batch.model_dump())

    try:
        # MetaTrader5 calls are blocking; run them off the event loop so a
        # slow MT5/broker round-trip doesn't stall other requests (e.g.
        # /health, or a webhook for a different strategy).
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, execute_order_batch, alert, targets)
    except OrderExecutionError as e:
        # Lock timeout (or similar) before any account ran — still honor
        # the 200 batch envelope with a synthetic failure result.
        logger.error("Order batch failed before results: %s", e)
        results = [
            AccountOrderResult(
                account=None,
                success=False,
                dry_run=dry_run,
                strategy=alert.strategy,
                symbol=alert.symbol,
                action=alert.order_id.value,
                message=str(e),
            )
        ]
    except Exception as e:
        logger.exception("Unexpected error during order execution")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "detail": str(e)},
        )

    batch = OrderBatchResponse(strategy=alert.strategy, results=results)
    asyncio.create_task(_notify_batch_async(results))
    return JSONResponse(status_code=status.HTTP_200_OK, content=batch.model_dump())
