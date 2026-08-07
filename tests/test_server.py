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

    def test_process_invalid_json_returns_error(self):
        """POST /process 传无效 JSON 应返回错误响应而非崩溃。"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/process", body="{invalid json", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        body = r.read()
        c.close()
        self.assertEqual(r.status, 200)  # HTTP 层不 500
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["success"])
        self.assertIn("无效的 JSON", data["output"])

    def test_process_empty_body_returns_error(self):
        """POST /process 传空 body 应返回错误响应而非崩溃。"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/process", body="", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        body = r.read()
        c.close()
        self.assertEqual(r.status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["success"])


class TestFetchRssSingleFlight(unittest.TestCase):
    """single-flight：缓存过期时并发请求只触发一次全量抓取。"""

    @classmethod
    def setUpClass(cls):
        # 直接 import server 需要 webui 在 sys.path（现有 HTTP 测试走子进程绕开）
        if WEBUI not in sys.path:
            sys.path.insert(0, WEBUI)
        # server.py 顶层用 sys.argv[1] 解析端口，unittest 会把测试名传进来；临时清理避免崩溃
        cls._saved_argv = sys.argv[:]
        sys.argv = [sys.argv[0]]
        try:
            import server as srv
        finally:
            sys.argv = cls._saved_argv
        cls.srv = srv

    def _reset_cache(self):
        self.srv.RSS_CACHE["ts"] = 0.0
        self.srv.RSS_CACHE["items"] = []

    def test_concurrent_requests_share_one_fetch(self):
        from unittest import mock
        call_count = {"n": 0}
        single_feed = [{"name": "A", "url": "https://a.com/feed"}]

        def fake_fetch_feed_items(feed, limit=2):
            call_count["n"] += 1
            return [{"title": f"T{feed['url']}", "url": f"https://x.com/{feed['url']}", "source": feed["name"], "ts": 0}]

        # patch _load_feeds 返回单源 + patch fetch_feed_items 计数，避免真实抓取 152 源
        with mock.patch.object(self.srv, "_load_feeds", return_value=single_feed):
            with mock.patch.object(self.srv, "fetch_feed_items", side_effect=fake_fetch_feed_items):
                self._reset_cache()
                # 并发发起 8 个请求（用线程模拟），缓存空 + force=False 走自然过期路径
                import threading
                results = [None] * 8
                threads = []
                # 用 barrier 保证线程真正同时启动，模拟"缓存刚过期瞬间"的并发
                barrier = threading.Barrier(8)
                for i in range(8):
                    def _run(idx=i):
                        try:
                            barrier.wait(timeout=5)
                        except Exception:
                            pass
                        results[idx] = self.srv._fetch_rss_items()
                    t = threading.Thread(target=_run)
                    threads.append(t)
                    t.start()
                for t in threads:
                    t.join(timeout=30)
        # 8 个并发请求应共享一次抓取（single-flight）
        self.assertEqual(call_count["n"], 1)
        for r in results:
            self.assertEqual(len(r), 1)


if __name__ == "__main__":
    unittest.main()
