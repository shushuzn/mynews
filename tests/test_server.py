"""webui/server.py 集成测试：缓存响应头 + 基本接口。

通过真实启动 HTTP 服务并请求接口，断言响应头与基本响应正确。
"""
import os
import sys
import time
import json
import socket
import http.client
import subprocess
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI = os.path.join(BASE, "webui")

os.environ.setdefault("FLOMO_TOKEN", "test-token")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestServerHTTP(unittest.TestCase):
    """真实启动 server.py，验证 HTTP 响应头与接口。"""

    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(WEBUI, "server.py"), str(cls.port)],
            cwd=WEBUI,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        # 等待服务就绪
        deadline = time.time() + 10
        cls.ready = False
        while time.time() < deadline:
            try:
                c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=1)
                c.request("GET", "/api/auto-bg")
                c.getresponse().read()
                c.close()
                cls.ready = True
                break
            except Exception:
                time.sleep(0.3)
        if not cls.ready:
            cls.proc.kill()
            raise RuntimeError("server.py 启动失败")

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait(timeout=5)

    def _get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        headers = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, headers, body

    def test_auto_bg_has_no_store(self):
        status, headers, body = self._get("/api/auto-bg")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("cache-control"), "no-store")
        data = json.loads(body.decode("utf-8"))
        self.assertIn("success", data)

    def test_index_has_no_cache(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("cache-control"), "no-cache")
        self.assertIn(b"<!DOCTYPE html>", body)


if __name__ == "__main__":
    unittest.main()
