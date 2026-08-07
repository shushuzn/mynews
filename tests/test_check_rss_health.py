"""check_rss_health.py 单元测试：check_one 的四种状态判定。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import check_rss_health as crh

RSS_OK = """<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>a</title><link>https://a.com/1</link></item>
  <item><title>b</title><link>https://a.com/2</link></item>
</channel></rss>""".encode("utf-8")

RSS_EMPTY = """<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>""".encode("utf-8")

RSS_BAD = b"not xml at all"


class FakeResp:
    def __init__(self, data):
        self._data = data
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        return self._data if n is None else self._data[:n]


class TestCheckOne(unittest.TestCase):
    def test_ok(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(RSS_OK)):
            r = crh.check_one({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["items"], 2)

    def test_empty(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(RSS_EMPTY)):
            r = crh.check_one({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(r["status"], "empty")

    def test_parse_error(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(RSS_BAD)):
            r = crh.check_one({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(r["status"], "parse_error")
        self.assertIn("detail", r)

    def test_network_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            r = crh.check_one({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(r["status"], "error")
        self.assertIn("OSError", r["detail"])

    # 源侧未转义的裸 &（如爱范儿 feed 的 &</image>）：应通过 parse_feed_xml 修复而非误报 parse_error
    RSS_RAW_AMP = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>Tech &amp; Science</title>
      <image>https://a.com/logo.png&</image>
      <item><title>a</title><link>https://a.com/1</link></item>
    </channel></rss>"""

    def test_raw_amp_not_misreported(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(self.RSS_RAW_AMP)):
            r = crh.check_one({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["items"], 1)


if __name__ == "__main__":
    unittest.main()
