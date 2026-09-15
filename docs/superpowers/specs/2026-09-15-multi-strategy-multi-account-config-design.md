# Multi-strategy multi-account config redesign

**Date:** 2026-09-15  
**Status:** Approved for planning (pending user final review of this file)  
**Repo:** mt5_bot

## Goal

Restructure configuration so that:

1. One **strategy** can contain many **accounts**.
2. Each account can override trading params (`price`, `deviation`, `magic`, etc.).
3. MT5 credentials live in `config.json` (`l` / `p`), not in `.env`.
4. Telegram is configured only via `.env` (global token + chat id).
5. `.env` is cleaned of per-strategy MT5 passwords and per-strategy Telegram tokens.

## Decisions (from brainstorming)

| Topic | Choice |
|-------|--------|
| Account targeting | **Hybrid (C):** default fan-out to all `enabled` accounts; optional alert field `account` selects one |
| Telegram | **A:** `.env` only — `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`; remove `telegram` block from `config.json` |
| Param layering | **B:** strategy defaults; account overrides; `magic` + `mt5` required on account |
| Account id | **C:** string key in config (e.g. `exness_main`); alert uses that key; `mt5.l` is the MT5 login number |
| Partial MT5 failure | **D:** always HTTP 200 after execution starts, with per-account `results[]`; hard 4xx only for unknown/disabled config |
| Config layout | **Approach 1:** nested `strategies.<name>.accounts.<key>` |
| MT5 concurrency | Keep **sequential** under existing `MT5_LOCK` this phase (one account after another; one session per process) |
| Telegram latency | **Non-blocking:** never await Telegram before HTTP response; short timeout; errors logged only |
| Credential field names | `mt5.l` = login, `mt5.p` = password (not `login` / `password`) |

## Non-goals (this phase)

- Parallel MT5 sessions / multi-process per account
- Shared global account registry (flat accounts referenced by many strategies)
- FTMO-specific config revival (not part of current live config path)
- Changing TradingView alert fields other than optional `account`
- Committing real secrets; `config.json` and `.env` remain gitignored

## Current state (baseline)

- `config.json`: `strategies.<key>` maps 1:1 to one MT5 account (`mt5.login` / `server`); trading params on strategy.
- `.env`: `MT5_PASSWORD_<STRATEGY>`, optional Telegram tokens, log settings, optional `DRY_RUN` / `MT5_TERMINAL_PATH`.
- Webhook `POST /api/order` resolves one `StrategyConfig`, executes under `MT5_LOCK`, awaits Telegram before returning.

## Target config shape

### `config.json`

```json
{
  "dryRun": false,
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
            "l": 11111111,
            "p": "secret",
            "server": "Exness-MT5Trial17"
          }
        },
        "xm_01": {
          "magic": 100002,
          "mt5": {
            "l": 22222222,
            "p": "secret",
            "server": "XMGlobal-Live01"
          }
        }
      }
    }
  }
}
```

### Field rules

**App**

- `dryRun` (bool): global default.

**Strategy**

- Defaults (optional unless noted): `price`, `deviation` (default 200 if unset at both levels), `comment`, optional `dryRun`.
- Required: `accounts` — non-empty object of account configs.
- Strategy-level `price` is required **as a default** unless every account sets its own `price` (implementation may require strategy `price` always present for simpler validation — prefer: strategy `price` required; accounts may override).

**Account**

- Key: string identifier used in alerts (`account` field).
- Required: `magic`, `mt5` (`l`, `p`, `server`).
- Optional: `enabled` (default `true`), `price`, `deviation`, `comment`, `dryRun`, `terminal_path` (or under `mt5`).
- Effective value resolution order for mergeable fields: **account → strategy → app** (`dryRun` only at app); then env `DRY_RUN` overrides all if set (preserve current behavior).

**`mt5`**

- `l` (int): MT5 login.
- `p` (string): MT5 password.
- `server` (string).
- Optional `terminal_path`: else `MT5_TERMINAL_PATH` env, else default path (current priority).

### `.env` (after cleanup)

**Keep / use:**

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `LOG_FILE`, `LOG_LEVEL` (optional)
- `DRY_RUN` (optional global override)
- `MT5_TERMINAL_PATH` (optional)
- `CONFIG_PATH` (optional)

**Remove:**

- `MT5_PASSWORD_*`
- `TELEGRAM_BOT_TOKEN_<STRATEGY>`
- Any documented use of `telegram.botToken` / `chatId` in JSON

If token or chat id missing: skip notify (no error).

`config.example.json` and `.env.example` must match the new schema (placeholder `p`, no real secrets).

## Webhook behavior

### Request

Existing `TradingViewAlert` fields unchanged, plus:

- `account` (optional string): account key under the named strategy.

### Target resolution

1. Unknown `strategy` → **400** `unknown_strategy`.
2. If `account` provided:
   - Missing key → **400** `unknown_account`.
   - `enabled=false` → **400** `account_disabled`.
3. If `account` omitted: all accounts with `enabled=true`.
   - None → **400** `no_enabled_accounts`.
4. Invalid/missing credentials or schema issues discovered at load/resolve → **400** `config_error`.

### Execution

- For each target account, sequentially under `MT5_LOCK`:
  - Connect with that account’s `l` / `p` / `server` (and terminal path).
  - Execute the same order action as today (open/close/closeAll, volume from effective `price * order_ratio`, magic/comment/deviation from resolved account config).
- One account’s connect/order failure does **not** stop remaining accounts.
- Dedupe remains at alert level (strategy + order_id + … as today); applies once per webhook, not per account.

### Response

After execution has started (targets resolved successfully):

- Always **HTTP 200**.
- Body envelope:

```json
{
  "strategy": "eth_strategy",
  "results": [
    {
      "account": "exness_main",
      "success": true,
      "dry_run": false,
      "strategy": "eth_strategy",
      "symbol": "ETHUSDT.P",
      "action": "openLong",
      "volume": 0.01,
      "price": 3500.0,
      "order_ticket": 123,
      "message": "..."
    },
    {
      "account": "xm_01",
      "success": false,
      "dry_run": false,
      "strategy": "eth_strategy",
      "symbol": "ETHUSDT.P",
      "action": "openLong",
      "message": "MT5 connect failed: ..."
    }
  ]
}
```

TradingView typically ignores body and only sees HTTP success — ops rely on Telegram + logs for per-account failures. This is an explicit trade-off (decision D) to avoid TV retries causing duplicate fills under multi-account / partial success.

**Breaking change:** single `OrderResult` response becomes envelope + `results[]`. TV webhooks that only check HTTP 2xx remain fine.

Config/validation errors before execution keep **4xx** as listed above. Unexpected process-level failures may still return **500**.

## Telegram (non-blocking)

- Read only `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from env.
- After building the HTTP response payload, **return immediately**; schedule notify in the background (`asyncio.create_task` or equivalent). Do **not** `await` Telegram before responding (current code awaits `to_thread(notify)` and can delay up to ~10s).
- Short HTTP timeout (e.g. 3s); catch/log all failures; never raise into the request path.
- Notify once per account result (include account key in message). Do not hold `MT5_LOCK` while sending Telegram.
- Missing token/chat → skip silently.

## Components to change

| Module | Change |
|--------|--------|
| `app/config.py` | New models; merge helpers; drop JSON telegram; password from `mt5.p` |
| `app/schemas.py` | Optional `account` on alert; multi-result response model |
| `app/main.py` | Resolve targets; loop accounts; 200 + `results[]`; fire-and-forget telegram |
| `app/order_service.py` | Execute against one resolved account config per call |
| `app/mt5_client.py` | Use `l` / `p` |
| `app/telegram_notifier.py` | Env-only; account in message; safe timeouts |
| Examples + RUNBOOK/DOCKER/README/HUONG_DAN as needed | Document new schema |
| `tests/*` | Cover merge, resolve, partial failure, no `MT5_PASSWORD_*`, telegram non-failure |

## Testing plan (minimum)

1. Strategy default `price` overridden by account.
2. Fan-out two enabled accounts; single-account filter via `account`.
3. One success + one fail → HTTP 200, both entries in `results[]`.
4. Credentials read from `mt5.p`; absence of `MT5_PASSWORD_*` does not break.
5. Telegram HTTP error does not change status/body of order response.
6. Unknown strategy / unknown account / disabled account → 400.

## Migration notes

1. Move each strategy’s password from `.env` `MT5_PASSWORD_*` into `config.json` `accounts.*.mt5.p`.
2. Nest former single-account strategy fields under `accounts.<key>`; choose a stable key name.
3. Move Telegram token/chat to `.env`; delete per-strategy telegram JSON.
4. Update local `config.json` / `.env` (gitignored); ship updated `*.example` files.
5. Restart bot / container after config change.

## Open points deferred

- Parallel execution across accounts/terminals (future phase).
- Whether strategy-level `price` can be omitted when all accounts set `price` (prefer required strategy `price` for YAGNI/simple validation unless implementation shows pain).
