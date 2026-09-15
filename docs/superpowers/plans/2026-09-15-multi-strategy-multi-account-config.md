# Multi-strategy multi-account config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure config so each strategy holds many accounts (`l`/`p` in JSON), fan-out or single-account webhook execution, Telegram only in `.env`, non-blocking notifications, HTTP 200 + `results[]`.

**Architecture:** Pydantic models in `app/config.py` for nested `accounts`; `resolve_execution_targets(strategy_name, account?)` returns `(strategy_name, list[(account_key, ResolvedAccountConfig)])`; `order_service.execute_order_batch` holds `MT5_LOCK` once and runs per-account `execute_order_for_account`; `main.py` builds envelope, returns 200, schedules Telegram in background.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, pytest, httpx (Telegram), fake MetaTrader5 in tests.

**Spec:** `docs/superpowers/specs/2026-09-15-multi-strategy-multi-account-config-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| `app/config.py` | Nested schema, merge helpers, target resolution, remove `get_mt5_password` / JSON telegram |
| `app/schemas.py` | `account` on alert; `AccountOrderResult`; `OrderBatchResponse` |
| `app/order_service.py` | Per-account execution; batch under one lock |
| `app/mt5_client.py` | `ensure_connection(resolved, password)` using `mt5.l` |
| `app/main.py` | Resolve targets, duplicate envelope, batch execute, async telegram |
| `app/telegram_notifier.py` | Env token/chat; `account` in message; 3s timeout |
| `config.example.json`, `.env.example` | New schema |
| `tests/test_config.py` | New: merge + resolve |
| `tests/conftest.py` | New nested config fixture |
| `tests/test_webhook.py`, `test_order_service.py`, `test_telegram_notifier.py`, `test_mt5_client.py` | Update for new shapes |
| `RUNBOOK.md`, `DOCKER.md`, `README.md` | Migration notes (minimal edits) |

---

### Task 1: Config models and resolution

**Files:**
- Modify: `app/config.py`
- Create: `tests/test_config.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write failing tests for schema and merge**

Create `tests/test_config.py`:

```python
import json

import pytest

from app.config import (
    ConfigError,
    ResolvedAccountConfig,
    get_config,
    resolve_execution_targets,
    resolve_account_config,
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


def test_resolve_account_merges_strategy_defaults(nested_config):
    resolved = resolve_account_config("eth_strategy_01", "acc_a")
    assert resolved.price == 1500
    assert resolved.deviation == 20
    assert resolved.comment == "default"
    assert resolved.magic == 100001
    assert resolved.mt5.l == 111
    assert resolved.mt5.p == "pass-a"


def test_fan_out_enabled_accounts_only(nested_config):
    keys = resolve_execution_targets("eth_strategy_01", account=None)
    assert keys == [("acc_a",)]  # only enabled; shape: list of (key,) tuples — see implementation


def test_single_account_target(nested_config):
    targets = resolve_execution_targets("eth_strategy_01", account="acc_a")
    assert len(targets) == 1
    assert targets[0][0] == "acc_a"


def test_unknown_account_raises(nested_config):
    with pytest.raises(ConfigError, match="unknown_account"):
        resolve_execution_targets("eth_strategy_01", account="nope")


def test_disabled_account_raises(nested_config):
    with pytest.raises(ConfigError, match="account_disabled"):
        resolve_execution_targets("eth_strategy_01", account="acc_b")


def test_no_enabled_accounts_raises(nested_config):
    # disable acc_a in fixture by patching config or second fixture
    pass  # implement in Step 3 with dedicated fixture
```

Adjust test expectations once `resolve_execution_targets` return type is finalized (recommended: `list[tuple[str, ResolvedAccountConfig]]`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nguyennam/WorkSpace/mt5_bot && pytest tests/test_config.py -v`  
Expected: FAIL (missing symbols / old flat schema)

- [ ] **Step 3: Implement config models and helpers in `app/config.py`**

Replace flat `StrategyConfig` with:

```python
class MT5Config(BaseModel):
    l: int
    p: str
    server: str
    terminal_path: Optional[str] = None


class AccountConfig(BaseModel):
    enabled: bool = True
    magic: int
    price: Optional[float] = None
    deviation: Optional[int] = None
    comment: Optional[str] = None
    dryRun: Optional[bool] = None
    mt5: MT5Config


class StrategyConfig(BaseModel):
    price: float
    deviation: int = 200
    comment: str = ""
    dryRun: Optional[bool] = None
    accounts: Dict[str, AccountConfig]


class ResolvedAccountConfig(BaseModel):
    account_key: str
    strategy_name: str
    price: float
    deviation: int
    magic: int
    comment: str
    dryRun: Optional[bool]
    mt5: MT5Config
```

Add:

```python
def resolve_account_config(strategy_name: str, account_key: str) -> ResolvedAccountConfig:
    strategy = get_strategy_config(strategy_name)
    account = strategy.accounts.get(account_key)
    if account is None:
        raise ConfigError(f"Unknown account '{account_key}' for strategy '{strategy_name}'")
    if not account.enabled:
        raise ConfigError(f"Account '{account_key}' is disabled for strategy '{strategy_name}'")
    return ResolvedAccountConfig(
        account_key=account_key,
        strategy_name=strategy_name,
        price=account.price if account.price is not None else strategy.price,
        deviation=account.deviation if account.deviation is not None else strategy.deviation,
        magic=account.magic,
        comment=account.comment if account.comment is not None else strategy.comment,
        dryRun=account.dryRun if account.dryRun is not None else strategy.dryRun,
        mt5=account.mt5,
    )


def resolve_execution_targets(
    strategy_name: str, account: Optional[str]
) -> list[ResolvedAccountConfig]:
    strategy = get_strategy_config(strategy_name)
    if account is not None:
        return [resolve_account_config(strategy_name, account)]
    enabled = [k for k, a in strategy.accounts.items() if a.enabled]
    if not enabled:
        raise ConfigError(f"No enabled accounts for strategy '{strategy_name}'")
    return [resolve_account_config(strategy_name, k) for k in enabled]
```

Update `is_dry_run` to accept `ResolvedAccountConfig` (or strategy + optional account dryRun):

```python
def is_dry_run(resolved: ResolvedAccountConfig) -> bool:
    env_override = os.getenv("DRY_RUN")
    if env_override is not None:
        return env_override.strip().lower() in ("1", "true", "yes", "on")
    if resolved.dryRun is not None:
        return resolved.dryRun
    return get_config().dryRun
```

Remove: `TelegramConfig`, `get_mt5_password`, `get_telegram_bot_token(strategy_name)`.

Add env helpers:

```python
def get_telegram_bot_token() -> Optional[str]:
    return os.getenv("TELEGRAM_BOT_TOKEN")


def get_telegram_chat_id() -> Optional[str]:
    return os.getenv("TELEGRAM_CHAT_ID")
```

Update `get_mt5_terminal_path` to accept `MT5Config` (use `mt5.terminal_path`).

- [ ] **Step 4: Complete `test_no_enabled_accounts_raises` and run config tests**

Run: `pytest tests/test_config.py -v`  
Expected: PASS

- [ ] **Step 5: Update `tests/conftest.py` `tmp_config` to nested schema**

```python
"strategies": {
    "eth_strategy_01": {
        "price": 1000,
        "deviation": 20,
        "comment": "ETH Strategy 01",
        "accounts": {
            "default": {
                "magic": 100001,
                "mt5": {"l": 12345678, "p": "test-password", "server": "TestServer"},
            }
        },
    }
}
```

Remove `monkeypatch.setenv("MT5_PASSWORD_ETH_STRATEGY_01", ...)`.

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_config.py tests/conftest.py
git commit -m "feat(config): nested strategies with accounts and resolve helpers"
```

---

### Task 2: Schemas and alert `account` field

**Files:**
- Modify: `app/schemas.py`
- Modify: `tests/test_schemas.py`

- [ ] **Step 1: Add failing tests**

In `tests/test_schemas.py`:

```python
def test_optional_account_field():
    alert = TradingViewAlert.model_validate(_base_payload(account="exness_main"))
    assert alert.account == "exness_main"


def test_account_defaults_to_none():
    alert = TradingViewAlert.model_validate(_base_payload())
    assert alert.account is None
```

Add tests for envelope models in same file or new `tests/test_batch_schemas.py`:

```python
from app.schemas import AccountOrderResult, OrderBatchResponse

def test_batch_response_shape():
    batch = OrderBatchResponse(
        strategy="s1",
        results=[
            AccountOrderResult(
                account="a1",
                success=True,
                dry_run=True,
                strategy="s1",
                symbol="ETH",
                action="openLong",
                message="ok",
            )
        ],
    )
    assert batch.results[0].account == "a1"
```

- [ ] **Step 2: Run tests — expect FAIL**

`pytest tests/test_schemas.py -v`

- [ ] **Step 3: Implement in `app/schemas.py`**

```python
class TradingViewAlert(BaseModel):
    ...
    strategy: str
    account: Optional[str] = None

    @field_validator("account")
    @classmethod
    def account_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class AccountOrderResult(BaseModel):
    account: Optional[str] = None
    success: bool
    dry_run: bool
    strategy: str
    symbol: str
    action: str
    volume: Optional[float] = None
    price: Optional[float] = None
    order_ticket: Optional[int] = None
    message: str


class OrderBatchResponse(BaseModel):
    strategy: str
    results: list[AccountOrderResult]
```

Keep `OrderResult` as alias or migrate internal uses to `AccountOrderResult` (prefer single type — deprecate `OrderResult` by making it an alias: `OrderResult = AccountOrderResult` for minimal churn).

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
git add app/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): optional account field and batch response envelope"
```

---

### Task 3: Order service — per-account and batch execution

**Files:**
- Modify: `app/order_service.py`
- Modify: `tests/test_order_service.py`

- [ ] **Step 1: Change signatures in tests — update imports to `ResolvedAccountConfig`, `AccountOrderResult`**

Replace `get_strategy_config` + `strategy` arg with:

```python
from app.config import resolve_account_config

strategy = resolve_account_config("eth_strategy_01", "default")
result = order_service.execute_order_for_account(alert, strategy)
```

- [ ] **Step 2: Refactor `order_service.py`**

- Rename internal uses of `StrategyConfig` to `ResolvedAccountConfig`.
- Replace `get_mt5_password(alert.strategy)` with `resolved.mt5.p`.
- Change `execute_order` → `execute_order_for_account(alert, resolved: ResolvedAccountConfig) -> AccountOrderResult` (set `account=resolved.account_key` on result).
- Add `execute_order_batch(alert, targets: list[ResolvedAccountConfig]) -> list[AccountOrderResult]`:

```python
def execute_order_batch(alert, targets: list[ResolvedAccountConfig]) -> list[AccountOrderResult]:
    if not mt5_client.MT5_LOCK.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        raise OrderExecutionError(...)  # same message as today
    results: list[AccountOrderResult] = []
    try:
        for resolved in targets:
            try:
                results.append(execute_order_for_account(alert, resolved, _lock_held=True))
            except (OrderExecutionError, MT5Error, ConfigError) as e:
                results.append(
                    AccountOrderResult(
                        account=resolved.account_key,
                        success=False,
                        dry_run=is_dry_run(resolved),
                        strategy=resolved.strategy_name,
                        symbol=strip_perpetual_suffix(alert.symbol),
                        action=alert.order_id.value,
                        message=str(e),
                    )
                )
    finally:
        mt5_client.MT5_LOCK.release()
    return results
```

Refactor `execute_order_for_account` so lock acquire/release only happens when `_lock_held=False` (batch path passes `_lock_held=True`).

- [ ] **Step 3: Run order service tests**

`pytest tests/test_order_service.py -v`  
Fix any assertion on `OrderResult` field names (`dry_run` unchanged).

- [ ] **Step 4: Commit**

```bash
git add app/order_service.py tests/test_order_service.py
git commit -m "feat(orders): execute per resolved account with batch lock"
```

---

### Task 4: MT5 client credentials

**Files:**
- Modify: `app/mt5_client.py`
- Modify: `tests/test_mt5_client.py` (if exists)

- [ ] **Step 1: Update `ensure_connection`**

```python
from app.config import ResolvedAccountConfig, get_mt5_terminal_path

def ensure_connection(resolved: ResolvedAccountConfig) -> None:
    terminal_path = get_mt5_terminal_path(resolved.mt5)
    login = resolved.mt5.l
    password = resolved.mt5.p
    server = resolved.mt5.server
    # rest unchanged: compare _current_connection login/server/path
```

- [ ] **Step 2: Update call sites in order_service to `ensure_connection(resolved)`**

- [ ] **Step 3: Run tests**

`pytest tests/test_mt5_client.py tests/test_order_service.py -v`

- [ ] **Step 4: Commit**

```bash
git add app/mt5_client.py
git commit -m "feat(mt5): use resolved account l/p for connection"
```

---

### Task 5: Webhook handler (`main.py`)

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_webhook.py`

- [ ] **Step 1: Update webhook tests for envelope**

```python
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
    ...
    body = resp.json()
    assert body["strategy"] == "eth_strategy_01"
    assert body["results"][0]["success"] is True
```

Update duplicate test:

```python
assert "Duplicate" in second.json()["results"][0]["message"]
assert second.json()["results"][0]["account"] is None
```

- [ ] **Step 2: Implement `receive_order`**

Flow:

1. Parse alert (unchanged).
2. `get_strategy_config(alert.strategy)` for unknown strategy **or** use `resolve_execution_targets` inside try/except `ConfigError` → map message to error codes:
   - `Unknown strategy` → `unknown_strategy`
   - `Unknown account` → `unknown_account`
   - `disabled` → `account_disabled`
   - `No enabled accounts` → `no_enabled_accounts`
3. Duplicate branch → `OrderBatchResponse` with one synthetic result (`account=alert.account`, `success=True`, duplicate message). Use `is_dry_run` with first resolved target if `account` set, else strategy-level dry run via a lightweight helper `is_dry_run_for_alert(strategy_name, account_key?)`.
4. `results = await asyncio.to_thread(execute_order_batch, alert, targets)`
5. Build `OrderBatchResponse`, return **200** always for execute path.
6. Schedule telegram without awaiting:

```python
async def _notify_batch_async(results: list[AccountOrderResult]) -> None:
    for r in results:
        await asyncio.to_thread(notify_order_result, r)

# after building response dict:
asyncio.create_task(_notify_batch_async(results))
return JSONResponse(status_code=200, content=batch.model_dump())
```

7. Remove 502 paths for order failures; keep 500 for unexpected exceptions before/during batch if not caught.

Change route decorator: `response_model=OrderBatchResponse`.

- [ ] **Step 3: Add tests** for `unknown_account`, `no_enabled_accounts` (mock config fixture).

- [ ] **Step 4: Run**

`pytest tests/test_webhook.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_webhook.py
git commit -m "feat(api): multi-account batch webhook with 200 envelope"
```

---

### Task 6: Telegram notifier

**Files:**
- Modify: `app/telegram_notifier.py`
- Modify: `tests/test_telegram_notifier.py`

- [ ] **Step 1: Rewrite tests — no `StrategyConfig` / `TelegramConfig`**

```python
def test_skips_when_no_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    notify_order_result(_result())  # should not raise


def test_includes_account_in_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    # mock httpx.post, assert text contains Account: acc_a
```

- [ ] **Step 2: Implement**

```python
def notify_order_result(result: AccountOrderResult) -> None:
    token = get_telegram_bot_token()
    chat_id = get_telegram_chat_id()
    if not token or not chat_id:
        return
    message = _format_message(result)  # add line Account: {result.account or '—'}
    try:
        httpx.post(..., timeout=3.0)
    except httpx.HTTPError as e:
        logger.warning(...)
```

Remove `strategy: StrategyConfig` parameter.

- [ ] **Step 3: Run tests**

`pytest tests/test_telegram_notifier.py -v`

- [ ] **Step 4: Commit**

```bash
git add app/telegram_notifier.py tests/test_telegram_notifier.py
git commit -m "feat(telegram): env-only notify with account label"
```

---

### Task 7: Example config and `.env.example`

**Files:**
- Modify: `config.example.json`
- Modify: `.env.example`

- [ ] **Step 1: Rewrite `config.example.json`** per spec (nested `accounts`, `l`/`p`, no `telegram` block).

- [ ] **Step 2: Rewrite `.env.example`**

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
LOG_FILE=logs/app.log
LOG_LEVEL=INFO
# DRY_RUN=true
# MT5_TERMINAL_PATH=...
```

Remove all `MT5_PASSWORD_*` and per-strategy telegram lines.

- [ ] **Step 3: Commit**

```bash
git add config.example.json .env.example
git commit -m "docs: update example config for multi-account schema"
```

---

### Task 8: Integration tests and full suite

**Files:**
- Create or extend: `tests/test_webhook_multi_account.py`

- [ ] **Step 1: Test fan-out mock**

Two accounts in `tmp_config`; mock `execute_order_batch` or real batch with mocked MT5; assert two results.

- [ ] **Step 2: Test partial failure → 200 with one success one fail**

- [ ] **Step 3: Test telegram failure does not affect response** (mock httpx to raise in background task — assert response already 200; optional: use short sleep in test if needed).

- [ ] **Step 4: Run full suite**

`pytest -v`  
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: multi-account webhook and partial failure cases"
```

---

### Task 9: Documentation

**Files:**
- Modify: `RUNBOOK.md`, `DOCKER.md`, `README.md` (sections on config / env only)

- [ ] **Step 1: Replace old strategy-flat + MT5_PASSWORD docs with nested accounts + `l`/`p` + Telegram in `.env`.**

- [ ] **Step 2: Note breaking API response `results[]` and HTTP 200 on MT5 errors.**

- [ ] **Step 3: Commit**

```bash
git add RUNBOOK.md DOCKER.md README.md
git commit -m "docs: multi-account config and webhook response"
```

---

### Task 10: Local migration (operator, not committed)

- [ ] **Step 1: User updates gitignored `config.json` and `.env` on deploy machine** per spec Migration notes (not part of CI).

---

## Self-review (spec coverage)

| Spec requirement | Task |
|------------------|------|
| Nested accounts | 1 |
| `l` / `p` in JSON | 1, 4 |
| Strategy defaults + account override | 1 |
| Hybrid fan-out / `account` field | 2, 5 |
| 400 error codes | 1, 5 |
| Sequential MT5 under lock | 3 |
| HTTP 200 + `results[]` | 5 |
| Duplicate envelope | 5 |
| Telegram env-only, non-blocking | 6, 5 |
| Examples + docs | 7, 9 |
| Test plan items 1–8 | 1, 5, 6, 8 |

## Post-implementation verification

```bash
cd /home/nguyennam/WorkSpace/mt5_bot
pytest -v
```

Manual: POST `/api/order` with strategy having 2 accounts, no `account` field → 2 results; with `"account": "key"` → 1 result.
