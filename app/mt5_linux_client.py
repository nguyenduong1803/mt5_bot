"""
mt5_linux_client.py — Drop-in thay thế MetaTrader5 trên Linux.

Khi chạy trên Linux (Docker), module này forward tất cả MT5 calls
đến mt5_proxy/server.py đang chạy dưới Wine Python trên port 8765.

Cách dùng trong mt5_client.py:
    import platform
    if platform.system() == "Windows":
        import MetaTrader5 as mt5
    else:
        from app import mt5_linux_client as mt5
"""
import json
import types
import urllib.error
import urllib.request

_PROXY_URL = "http://127.0.0.1:8765"


class _MT5Error(Exception):
    pass


def _call(fn: str, args=None, kwargs=None):
    """Gọi một hàm MT5 qua HTTP proxy."""
    payload = json.dumps({"fn": fn, "args": args or [], "kwargs": kwargs or {}}).encode()
    req = urllib.request.Request(
        f"{_PROXY_URL}/",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise _MT5Error(
            f"MT5 proxy không phản hồi tại {_PROXY_URL} — "
            f"kiểm tra Wine Python proxy có đang chạy không. ({exc})"
        )
    if not data.get("ok"):
        raise _MT5Error(data.get("error", "Unknown proxy error"))
    return data.get("result")


def _make_namespace(d):
    """Chuyển dict thành namespace để truy cập bằng dấu chấm (obj.field)."""
    if d is None:
        return None
    if not isinstance(d, dict):
        return d
    ns = types.SimpleNamespace(**{k: _make_namespace(v) for k, v in d.items()})
    # Thêm _asdict() để code cũ dùng namedtuple vẫn hoạt động
    ns._asdict = lambda: d
    return ns


# ---------------------------------------------------------------------------
# Load constants từ proxy một lần khi module được import
# ---------------------------------------------------------------------------
def _load_constants():
    try:
        return _call("constants")
    except Exception:
        # Proxy chưa sẵn sàng (unit test, dry_run, v.v.) — dùng giá trị chuẩn
        return {
            "ORDER_FILLING_IOC": 2,
            "ORDER_FILLING_FOK": 1,
            "ORDER_FILLING_RETURN": 3,
            "ORDER_TYPE_BUY": 0,
            "ORDER_TYPE_SELL": 1,
            "TRADE_ACTION_DEAL": 1,
            "ORDER_TIME_GTC": 1,
            "TRADE_RETCODE_DONE": 10009,
            "TRADE_RETCODE_REQUOTE": 10004,
            "TRADE_RETCODE_PRICE_CHANGED": 10006,
            "POSITION_TYPE_BUY": 0,
            "POSITION_TYPE_SELL": 1,
            "SYMBOL_TRADE_MODE_DISABLED": 0,
        }


_consts = _load_constants()

# Expose constants như attributes của module
ORDER_FILLING_IOC           = _consts["ORDER_FILLING_IOC"]
ORDER_FILLING_FOK           = _consts["ORDER_FILLING_FOK"]
ORDER_FILLING_RETURN        = _consts["ORDER_FILLING_RETURN"]
ORDER_TYPE_BUY              = _consts["ORDER_TYPE_BUY"]
ORDER_TYPE_SELL             = _consts["ORDER_TYPE_SELL"]
TRADE_ACTION_DEAL           = _consts["TRADE_ACTION_DEAL"]
ORDER_TIME_GTC              = _consts["ORDER_TIME_GTC"]
TRADE_RETCODE_DONE          = _consts["TRADE_RETCODE_DONE"]
TRADE_RETCODE_REQUOTE       = _consts["TRADE_RETCODE_REQUOTE"]
TRADE_RETCODE_PRICE_CHANGED = _consts["TRADE_RETCODE_PRICE_CHANGED"]
POSITION_TYPE_BUY           = _consts["POSITION_TYPE_BUY"]
POSITION_TYPE_SELL          = _consts["POSITION_TYPE_SELL"]
SYMBOL_TRADE_MODE_DISABLED  = _consts["SYMBOL_TRADE_MODE_DISABLED"]


# ---------------------------------------------------------------------------
# MT5 API functions — forwarded to proxy
# ---------------------------------------------------------------------------

def initialize(path=None, login=None, password=None, server=None, **kwargs):
    kw = {}
    if path is not None:
        kw["path"] = path
    if login is not None:
        kw["login"] = login
    if password is not None:
        kw["password"] = password
    if server is not None:
        kw["server"] = server
    return _call("initialize", kwargs=kw)


def shutdown():
    return _call("shutdown")


def terminal_info():
    result = _call("terminal_info")
    return _make_namespace(result)


def last_error():
    result = _call("last_error")
    return tuple(result) if result else (0, "")


def symbol_info(symbol: str):
    result = _call("symbol_info", args=[symbol])
    return _make_namespace(result)


def symbol_select(symbol: str, enable: bool) -> bool:
    return _call("symbol_select", args=[symbol, enable])


def symbol_info_tick(symbol: str):
    result = _call("symbol_info_tick", args=[symbol])
    return _make_namespace(result)


def positions_get(symbol: str = None, **kwargs):
    kw = {}
    if symbol is not None:
        kw["symbol"] = symbol
    kw.update(kwargs)
    result = _call("positions_get", kwargs=kw)
    if result is None:
        return None
    return [_make_namespace(p) for p in result]


def order_send(request: dict):
    result = _call("order_send", args=[request])
    return _make_namespace(result)
