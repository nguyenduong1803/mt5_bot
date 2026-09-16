"""
MT5 Proxy Server — chạy dưới Wine Python trên localhost:8765
Expose MetaTrader5 functions qua HTTP JSON API để Linux Python gọi được.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import MetaTrader5 as mt5
except ImportError as e:
    print(f"FATAL: Cannot import MetaTrader5: {e}", flush=True)
    sys.exit(1)


def _serialize(obj):
    """Chuyển MT5 namedtuple/object thành JSON-serializable dict."""
    if obj is None:
        return None
    if isinstance(obj, (int, float, str, bool)):
        return obj
    if hasattr(obj, "_asdict"):          # namedtuple (Tick, SymbolInfo, ...)
        return {k: _serialize(v) for k, v in obj._asdict().items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    return str(obj)


class MT5Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress request logs

    def _respond(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._respond({"ok": True})
        else:
            self._respond({"ok": False, "error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length))
        fn = req.get("fn", "")
        args = req.get("args", [])
        kwargs = req.get("kwargs", {})

        try:
            result = self._dispatch(fn, args, kwargs)
            self._respond({"ok": True, "result": result})
        except Exception as exc:
            self._respond({"ok": False, "error": str(exc)})

    def _dispatch(self, fn, args, kwargs):
        if fn == "constants":
            return {
                "ORDER_FILLING_IOC":        mt5.ORDER_FILLING_IOC,
                "ORDER_FILLING_FOK":        mt5.ORDER_FILLING_FOK,
                "ORDER_FILLING_RETURN":     mt5.ORDER_FILLING_RETURN,
                "ORDER_TYPE_BUY":           mt5.ORDER_TYPE_BUY,
                "ORDER_TYPE_SELL":          mt5.ORDER_TYPE_SELL,
                "TRADE_ACTION_DEAL":        mt5.TRADE_ACTION_DEAL,
                "ORDER_TIME_GTC":           mt5.ORDER_TIME_GTC,
                "TRADE_RETCODE_DONE":       mt5.TRADE_RETCODE_DONE,
                "TRADE_RETCODE_REQUOTE":    mt5.TRADE_RETCODE_REQUOTE,
                "TRADE_RETCODE_PRICE_CHANGED": mt5.TRADE_RETCODE_PRICE_CHANGED,
                "POSITION_TYPE_BUY":        mt5.POSITION_TYPE_BUY,
                "POSITION_TYPE_SELL":       mt5.POSITION_TYPE_SELL,
                "SYMBOL_TRADE_MODE_DISABLED": mt5.SYMBOL_TRADE_MODE_DISABLED,
            }
        elif fn == "initialize":
            return mt5.initialize(**kwargs)
        elif fn == "shutdown":
            mt5.shutdown()
            return True
        elif fn == "terminal_info":
            return _serialize(mt5.terminal_info())
        elif fn == "account_info":
            return _serialize(mt5.account_info())
        elif fn == "last_error":
            code, msg = mt5.last_error()
            return [code, msg]
        elif fn == "symbol_info":
            return _serialize(mt5.symbol_info(args[0]))
        elif fn == "symbol_select":
            return mt5.symbol_select(args[0], args[1])
        elif fn == "symbol_info_tick":
            return _serialize(mt5.symbol_info_tick(args[0]))
        elif fn == "positions_get":
            positions = mt5.positions_get(**kwargs)
            return _serialize(positions) if positions is not None else []
        elif fn == "order_send":
            return _serialize(mt5.order_send(args[0]))
        else:
            raise ValueError(f"Unknown function: {fn!r}")


if __name__ == "__main__":
    port = 8765
    print(f"[MT5 Proxy] Starting on 127.0.0.1:{port} ...", flush=True)
    server = HTTPServer(("127.0.0.1", port), MT5Handler)
    print(f"[MT5 Proxy] Ready.", flush=True)
    server.serve_forever()
