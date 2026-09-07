import asyncio
import json

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import ConfigError, get_strategy_config, is_dry_run
from app.dedupe import is_duplicate
from app.logging_config import logger
from app.order_service import OrderExecutionError, execute_order
from app.schemas import OrderResult, TradingViewAlert
from app.telegram_notifier import notify_order_result

app = FastAPI(
    title="MT5 TradingView Webhook Bot",
    description="Receives TradingView webhook alerts and executes orders on MetaTrader 5 (Exness, FTMO)",
    version="1.0.0",
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/order", response_model=OrderResult)
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
        strategy = get_strategy_config(alert.strategy)
    except ConfigError as e:
        logger.error("Unknown strategy '%s': %s", alert.strategy, e)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "unknown_strategy", "detail": str(e)},
        )

    logger.info(
        "Processing order: strategy=%s symbol=%s order_id=%s dry_run=%s",
        alert.strategy,
        alert.symbol,
        alert.order_id.value,
        is_dry_run(strategy),
    )

    if is_duplicate(alert):
        logger.warning(
            "Duplicate alert ignored (same strategy/order_id/symbol/comment/"
            "timenow seen recently): strategy=%s order_id=%s",
            alert.strategy,
            alert.order_id.value,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=OrderResult(
                success=True,
                dry_run=is_dry_run(strategy),
                strategy=alert.strategy,
                symbol=alert.symbol,
                action=alert.order_id.value,
                message="Duplicate alert ignored (already processed this signal recently)",
            ).model_dump(),
        )

    try:
        # MetaTrader5 calls are blocking; run them off the event loop so a
        # slow MT5/broker round-trip doesn't stall other requests (e.g.
        # /health, or a webhook for a different strategy).
        result = await asyncio.to_thread(execute_order, alert, strategy)
    except OrderExecutionError as e:
        logger.error("Order execution failed: %s", e)
        await asyncio.to_thread(
            notify_order_result,
            strategy,
            OrderResult(
                success=False,
                dry_run=is_dry_run(strategy),
                strategy=alert.strategy,
                symbol=alert.symbol,
                action=alert.order_id.value,
                message=str(e),
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": "order_execution_failed", "detail": str(e)},
        )
    except ConfigError as e:
        logger.error("Config error during order execution: %s", e)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "config_error", "detail": str(e)},
        )
    except Exception as e:
        logger.exception("Unexpected error during order execution")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "detail": str(e)},
        )

    await asyncio.to_thread(notify_order_result, strategy, result)

    status_code = status.HTTP_200_OK if result.success else status.HTTP_502_BAD_GATEWAY
    return JSONResponse(status_code=status_code, content=result.model_dump())
