# MT5 TradingView Webhook Bot

FastAPI service that receives TradingView webhook alerts and executes market
orders on MetaTrader 5 (Exness) accounts.

## Features

- `POST /api/order` accepts raw `text/plain` bodies (as sent by TradingView),
  manually parses the string as JSON, then validates it with Pydantic.
- Maps `order_id` to trade actions:
  - `openLong` → BUY (reverses first: closes any open SHORT position(s) on
    the same symbol/magic before opening the LONG)
  - `closeLong` → close open LONG position(s)
  - `openShort` → SELL (reverses first: closes any open LONG position(s)
    before opening the SHORT)
  - `closeShort` → close open SHORT position(s)
- `strategy` in the payload selects the strategy from `config.json`. Each
  strategy holds nested `accounts` (magic, MT5 `l`/`p`/`server`, optional
  overrides). The **symbol to trade comes from the webhook payload**
  (`symbol`), not from `config.json` — configure your TradingView alert to
  send the exact MT5 symbol name (e.g. `ETHUSD`).
- Optional alert field `account` targets one account key; omit it to fan out
  to all `enabled` accounts under that strategy.
- Position size: `investment = effective_price * order_ratio` (strategy
  `price`, optionally overridden per account, in **USD**; `order_ratio` from
  the webhook scales it), `volume = investment / market_price`
  where `market_price` is the live bid/ask fetched from MT5 at order time,
  then normalized to the symbol's `volume_min` / `volume_max` / `volume_step`.
- `dryRun` mode (global in `config.json`, per-strategy / per-account
  override, or via `DRY_RUN` env var) validates and logs the order without
  sending it to MT5.
- MT5 credentials live in `config.json` as `accounts.*.mt5.l` / `mt5.p`
  (login / password), not in `.env`.
- Structured logging to console + rotating file (`logs/app.log`).
- Optional Telegram notification (global `TELEGRAM_BOT_TOKEN` +
  `TELEGRAM_CHAT_ID` in `.env` only — no `botToken` in config) for every
  account result, sent in the background after the HTTP response.

## Requirements

- Windows, with MetaTrader 5 terminal installed and logged in (or logged out
  — the app logs in using the configured account) — required only to place
  **real** orders. `dryRun: true` works anywhere the `MetaTrader5` package
  can be imported.
- Python 3.10+

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy the example config and env files:

```bash
copy config.example.json config.json
copy .env.example .env
```

Edit `config.json`:

```json
{
  "dryRun": true,
  "strategies": {
    "eth_strategy": {
      "price": 1000,
      "deviation": 200,
      "comment": "ETH default",
      "accounts": {
        "exness_main": {
          "enabled": true,
          "magic": 100001,
          "price": 1500,
          "mt5": {
            "l": 12345678,
            "p": "your-mt5-password-here",
            "server": "Exness-MT5Trial"
          }
        },
        "xm_01": {
          "magic": 100002,
          "mt5": {
            "l": 87654321,
            "p": "your-mt5-password-here",
            "server": "XMGlobal-Live01"
          }
        }
      }
    }
  }
}
```

- `price` on the strategy is required base capital in **USD** (`1000` = $1,000).
  Accounts may override `price`. Actual investment per signal is
  `effective_price * order_ratio`.
- `deviation` is the **starting** max allowed slippage (in points) for a
  market order. It is not the whole story: if MT5 rejects a fill for being
  too far from this (retcode Requote / Price Changed — the only rejections
  a fresh price can actually fix), the bot automatically retries with a
  fresh tick and a 3x wider deviation, up to **5 attempts total**, capped
  at **25x** the starting deviation (so it can't grow unbounded) and with a
  short delay between attempts so the next tick is a genuinely fresh quote
  (see `mt5_client.send_order_with_retry`). With `deviation: 200` the
  sequence is 200 → 600 → 1800 → 5000 → 5000. This keeps the first attempt
  price-protective while still giving a real, fast-moving market several
  real chances to fill — it does not *guarantee* a fill (a structural
  rejection like market-closed or trading-disabled isn't retried, since no
  amount of retrying fixes that), but it makes losing a legitimate signal
  to ordinary slippage very unlikely. Tune the starting value per symbol —
  `200` is a reasonable starting point for a volatile crypto CFD like
  ETHUSD, but forex majors typically need far less. Set on strategy and/or
  override per account (default **200** if unset at both levels).
- `magic` is required **per account** — the MT5 "magic number" tagged on
  every order that account places, so `closeLong`/`closeShort` only ever
  close positions this bot opened on that account (never a manual trade or
  another EA's position on the same account/symbol).
- `mt5.l` / `mt5.p` / `mt5.server` are required per account (login, password,
  server). Password is **not** read from `.env`.
- Optional `mt5.terminal_path` per account; else `MT5_TERMINAL_PATH` in
  `.env`; else the default Windows path
  (`C:/Program Files/MetaTrader 5/terminal64.exe`).
- Telegram is **not** configured in `config.json`. Set
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` only (see below).
- There is no `symbol` in `config.json` — the symbol to trade comes straight
  from the webhook payload's `symbol` field, so make sure it matches the
  broker's MT5 symbol name (e.g. `ETHUSD`), not an unrelated TradingView
  ticker. The one exception: a trailing `.P` (TradingView's
  perpetual-futures suffix, e.g. `BTCUSDT.P`) is automatically stripped
  before the symbol is used for anything MT5-related (see
  `app/symbols.py`), so sending `{{ticker}}` as-is for a `.P` symbol works
  without any extra mapping.
- `dryRun` can be set per-strategy or per-account (account → strategy →
  app); the `DRY_RUN` env var overrides all. Dry run still connects to MT5
  to read the live bid/ask for an accurate volume estimate — it only skips
  sending the actual order.

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=123456789:AAExampleBotTokenXXXXXXXXXXXXXXXXXXX
TELEGRAM_CHAT_ID=-1001234567890
LOG_LEVEL=INFO
LOG_FILE=logs/app.log
```

No `MT5_PASSWORD_*` — passwords live in `config.json` as `mt5.p`.
Omit either Telegram var to skip notifications (no error).

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## TradingView alert setup

- Webhook URL: `https://your-host/api/order`
- Message body (raw text, TradingView sends this as `text/plain`):

```json
{
  "symbol": "{{ticker}}",
  "price": "{{strategy.order.price}}",
  "alert_name": "{{alert_name}}",
  "timenow": "{{timenow}}",
  "order_id": "{{strategy.order.comment}}",
  "order_action": "{{strategy.order.action}}",
  "comment": "{{strategy.order.comment}}",
  "alert_message": null,
  "order_ratio": 1,
  "strategy": "eth_strategy"
}
```

- `order_id` (populated from `strategy.order.comment`) must resolve to one
  of `openLong`, `closeLong`, `openShort`, `closeShort` — set this via your
  Pine Script strategy's order comment.
- `strategy` must match a key under `strategies` in `config.json`.
- Optional `account` (string): account key under that strategy (e.g.
  `"exness_main"`). Omit to fan out to all `enabled` accounts.
- `symbol` (`{{ticker}}`) is used as the MT5 symbol to trade, with one
  automatic adjustment: a trailing `.P` (TradingView's perpetual-futures
  suffix) is stripped, so `{{ticker}}` resolving to `BTCUSDT.P` trades
  `BTCUSDT`. If your broker's symbol name differs from TradingView's ticker
  in any other way, hardcode the correct MT5 symbol in the alert message
  instead of using `{{ticker}}`.

## Telegram notifications

Set global credentials in `.env` only (no `telegram` / `botToken` in
`config.json`):

1. Create a bot via [@BotFather](https://t.me/BotFather), get its token.
2. Add the bot to the chat/group/channel you want notified, then get the
   chat id (e.g. message the bot and check
   `https://api.telegram.org/bot<TOKEN>/getUpdates`, or use a helper bot
   like @userinfobot / @getidsbot for a personal chat/group id — channel
   ids and group ids are usually negative numbers).
3. In `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAExampleBotTokenXXXXXXXXXXXXXXXXXXX
   TELEGRAM_CHAT_ID=-1001234567890
   ```
4. Omit either var to disable notifications.

Notifications are fire-and-forget after the HTTP response (never delay
TradingView). Failures (bad token, network error, etc.) are logged as
warnings only. One message per account result (includes the account key).

Message format — success/dry-run:

```
✅ ETHUSD-openLong: 4.125,10
Strategy: eth_strategy
Account: exness_main
```

Failure (adds a Message line):

```
❌ ETHUSD-closeLong: —
Strategy: eth_strategy
Account: exness_main
Message: No open LONG position found for symbol=ETHUSD magic=100001
```

The header line is `<symbol>-<action>: <fill price>`, numbers formatted
Vietnamese-style (`.` for thousands, `,` for decimals; `—` when there's no
price, e.g. a failed close). A trailing `.P` (TradingView's
perpetual-futures suffix, e.g. `ETHUSDT.P`) is stripped from the displayed
symbol. The status emoji is `✅` success / `❌` failure / `🧪` dry run.

`/api/order` has no built-in authentication — anyone who knows the URL can
call it. If you expose the server to the internet, restrict access at the
network level (firewall, VPN, or a reverse proxy in front of it).

## Request/response examples

Request:

```
POST /api/order
Content-Type: text/plain

{"symbol":"ETHUSD","price":"4123.45","order_id":"openLong","order_ratio":1,"strategy":"eth_strategy"}
```

Success response (HTTP **200**, envelope — one entry per targeted account):

```json
{
  "strategy": "eth_strategy",
  "results": [
    {
      "account": "exness_main",
      "success": true,
      "dry_run": true,
      "strategy": "eth_strategy",
      "symbol": "ETHUSD",
      "action": "openLong",
      "volume": 1.0,
      "price": 4123.45,
      "order_ticket": null,
      "message": "Dry run: no order sent to MT5"
    }
  ]
}
```

**Breaking change:** body is always `{strategy, results:[{account,...}]}`.
MT5 failures that used to return HTTP **502** now return **200** with
`results[].success=false` (ops via Telegram/logs). Only pre-execution
config errors stay **4xx**.

Error responses use a `{"error": "<code>", "detail": ...}` shape with an
appropriate HTTP status:

| Status | error                    | Cause                                   |
|--------|--------------------------|------------------------------------------|
| 400    | `invalid_json`           | Body is not valid JSON                   |
| 400    | `invalid_payload`        | JSON body is not an object                |
| 422    | `validation_error`       | Missing/invalid fields (Pydantic)         |
| 400    | `unknown_strategy`       | `strategy` not found in `config.json`     |
| 400    | `unknown_account`        | `account` not found under that strategy   |
| 400    | `account_disabled`       | Targeted account has `enabled: false`     |
| 400    | `no_enabled_accounts`    | Fan-out with no enabled accounts          |
| 400    | `config_error`           | Bad/incomplete account config             |
| 500    | `internal_error`         | Unexpected server error                    |

## Tests

```bash
pytest -q
```

Tests fake the `MetaTrader5` module (installed via `sys.modules`) so the
full suite runs without a real MT5 terminal, on any OS. Covers:

- payload parsing/validation (`tests/test_schemas.py`)
- volume normalization and order-building logic (`tests/test_order_service.py`)
- the HTTP webhook, including raw `text/plain` parsing and error paths
  (`tests/test_webhook.py`)

## Project layout

```
app/
  main.py            FastAPI app, /api/order endpoint, raw-body parsing
  schemas.py          Pydantic models for the TradingView payload
  config.py            config.json + .env loading, per-strategy lookups
  mt5_client.py         thin wrapper around the MetaTrader5 package
  order_service.py      order sizing, open/close logic, dry-run handling
  logging_config.py     console + rotating file logging
config.example.json    example config (copy to config.json)
.env.example            example secrets file (copy to .env)
tests/                  pytest suite with a faked MetaTrader5 module
```

## Notes / caveats

- The `MetaTrader5` Python package maintains a single connection per
  process. If you run multiple strategies against different MT5 terminal
  installations/accounts, the app reconnects (`mt5.initialize(...)`) before
  each order if the target account differs from the currently connected one.
- `closeLong` / `closeShort` close **all** open positions on the webhook's
  `symbol` that match the strategy's `magic` number and the requested side —
  not a specific ticket, since TradingView alerts don't carry one.
- `openLong` / `openShort` reverse an opposite existing position first:
  before opening, the bot closes any open position(s) on the *other* side
  (same symbol/magic) — so `openLong` while SHORT is open closes the SHORT
  then opens the LONG, and vice versa. This uses the same matching (symbol
  + magic) as `closeLong`/`closeShort`. The reversal result is prefixed
  onto the open order's `message` (e.g. `"Reversed: closed 1 SHORT
  position(s). Request executed"`); if closing the opposite side fails,
  the bot still attempts to open the new position.
- Position sizing: `investment = config.price * order_ratio`,
  `volume = investment / market_price`, where `market_price` is the live
  ask (for opens/longs) or bid (for opens/shorts, and closes) fetched from
  MT5 at order time — not the `price` field in the webhook payload, which is
  only used for logging/context. Volume is then normalized against the
  symbol's `volume_min/max/step`.
- Dry-run mode still opens a real MT5 connection to read live bid/ask (so it
  can report a realistic volume), it just skips `order_send`.
