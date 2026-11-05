"""OpenD-up serves OpenD; OpenD-down serves Yahoo; both label the feed."""
import json
import os
import socket
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

import pandas as pd

PORT = 28711


class _KLType:
    K_15M = "K_15M"
    K_60M = "K_60M"
    K_DAY = "K_DAY"


class _SubType:
    QUOTE = "QUOTE"


class FakeFutu:
    RET_OK = 0
    contexts = []
    kline_delay = 0
    kline_ktypes = []


class _OpenQuoteContext:
    def __init__(self, host="127.0.0.1", port=11111):
        self.host = host
        self.port = port
        FakeFutu.contexts.append(self)

    def subscribe(self, *args, **kwargs):
        raise RuntimeError("kline subscribe is not required")

    def get_market_snapshot(self, symbols):
        frame = pd.DataFrame([{"last_price": 321.0, "prev_close_price": 300.0}])
        return FakeFutu.RET_OK, frame

    def request_history_kline(self, *args, **kwargs):
        FakeFutu.kline_ktypes.append(kwargs.get("ktype"))
        if FakeFutu.kline_delay:
            time.sleep(FakeFutu.kline_delay)
        start = int(time.time()) - 8 * 3600
        rows = []
        for i in range(8):
            price = 300.0 + i
            rows.append({
                "time_key": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(start + i * 3600)),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 1000 + i * 10,
            })
        return FakeFutu.RET_OK, pd.DataFrame(rows), None

    def close(self):
        return None


def _install_fake_futu():
    module = types.ModuleType("futu")
    module.OpenQuoteContext = _OpenQuoteContext
    module.KLType = _KLType
    module.SubType = _SubType
    module.RET_OK = FakeFutu.RET_OK
    sys.modules["futu"] = module


def _yahoo_payload():
    now = int(time.time())
    timestamps = [now - (8 - i) * 3600 for i in range(8)]
    flat = [50.0] * 8
    return {
        "chart": {
            "result": [{
                "meta": {
                    "regularMarketPrice": 55.5,
                    "regularMarketPreviousClose": 50.0,
                    "previousClose": 50.0,
                },
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open": flat,
                        "high": [51.0] * 8,
                        "low": [49.0] * 8,
                        "close": flat,
                        "volume": [1000] * 8,
                    }]
                },
            }]
        }
    }


class _YahooResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _AcceptThread(threading.Thread):
    def __init__(self, port):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.close()

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


_install_fake_futu()
import server  # noqa: E402


class SourceRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.listener = _AcceptThread(PORT)
        cls.listener.start()
        os.environ["FUTU_PORT"] = str(PORT)

    @classmethod
    def tearDownClass(cls):
        cls.listener.stop()
        os.environ.pop("FUTU_HOST", None)
        os.environ.pop("FUTU_PORT", None)

    def setUp(self):
        acquired = server._radar_scan_lock.acquire(timeout=5)
        if acquired:
            server._radar_scan_lock.release()
        server.RADAR_CACHE.clear()
        server.RADAR_STATE["last_data_source"] = None
        server.RADAR_STATE["last_source_mode"] = None
        server.RADAR_STATE["is_running"] = True
        server.RADAR_STATE["scan_cursor"] = 0
        FakeFutu.contexts.clear()
        FakeFutu.kline_ktypes.clear()
        FakeFutu.kline_delay = 0
        os.environ["FUTU_PORT"] = str(PORT)
        os.environ.pop("FUTU_HOST", None)
        self.client = server.app.test_client()

    def test_source_file_has_no_tailscale_host(self):
        path = os.path.join(os.path.dirname(__file__), "server.py")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(".ts.net", text)

    def test_opend_up_quote_and_radar_use_opend(self):
        os.environ["FUTU_HOST"] = "https://funnel.example/opend"
        yahoo_calls = []

        def fail_yahoo(*args, **kwargs):
            yahoo_calls.append(args)
            raise AssertionError("Yahoo was called while OpenD was up")

        with patch("urllib.request.urlopen", fail_yahoo):
            quote = self.client.get("/api/quote?symbols=NVDA&interval=4h&chart=0")
            self.assertEqual(quote.status_code, 200)
            body = quote.get_json()["NVDA"]
            self.assertEqual(body["data_source"], "FUTU_OPEND")
            self.assertEqual(body["source_mode"], "OPEND")
            self.assertEqual(body["price"], 321.0)
            self.assertEqual(FakeFutu.contexts[-1].host, "127.0.0.1")
            self.assertEqual(FakeFutu.contexts[-1].port, PORT)
            self.assertEqual(FakeFutu.kline_ktypes[-1], _KLType.K_60M)

            started = time.perf_counter()
            radar = self.client.get("/api/radar")
            elapsed = time.perf_counter() - started
            self.assertEqual(radar.status_code, 200)
            status = radar.get_json()["status"]
            self.assertEqual(status["source_mode"], "OPEND")
            self.assertEqual(status["data_source"], "FUTU_OPEND")
            self.assertLess(elapsed, 1.2)
            deadline = time.time() + 2
            while server.RADAR_STATE.get("last_data_source") != "FUTU_OPEND" and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(server.RADAR_STATE["last_data_source"], "FUTU_OPEND")
            self.assertEqual(server.RADAR_STATE["last_source_mode"], "OPEND")
        self.assertEqual(yahoo_calls, [])

    def test_radar_returns_before_slow_opend(self):
        FakeFutu.kline_delay = 1.5
        started = time.perf_counter()
        radar = self.client.get("/api/radar")
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)
        status = radar.get_json()["status"]
        self.assertEqual(status["source_mode"], "OPEND")
        self.assertEqual(status["data_source"], "FUTU_OPEND")
        deadline = time.time() + 3
        while server.RADAR_STATE.get("last_data_source") != "FUTU_OPEND" and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(server.RADAR_STATE["last_data_source"], "FUTU_OPEND")

    def test_opend_down_uses_yahoo(self):
        os.environ["FUTU_PORT"] = "1"  # closed port; not an OpenD listener
        before = len(FakeFutu.contexts)
        yahoo_calls = []

        def fake_urlopen(req, timeout=5):
            yahoo_calls.append(req.full_url)
            return _YahooResponse(_yahoo_payload())

        with patch("urllib.request.urlopen", fake_urlopen):
            started = time.perf_counter()
            radar = self.client.get("/api/radar")
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 1.2)
            status = radar.get_json()["status"]
            self.assertEqual(status["source_mode"], "YAHOO")
            self.assertEqual(status["data_source"], "YAHOO")

            quote = self.client.get("/api/quote?symbols=NVDA&interval=4h&chart=0")
            body = quote.get_json()["NVDA"]
            self.assertEqual(body["data_source"], "YAHOO")
            self.assertEqual(body["source_mode"], "YAHOO")
            self.assertEqual(body["price"], 55.5)
            self.assertTrue(any("NVDA" in url for url in yahoo_calls))
        self.assertEqual(len(FakeFutu.contexts), before)


if __name__ == "__main__":
    unittest.main()
