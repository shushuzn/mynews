"""rss_utils.py 单元测试：OPML 解析、时间戳解析、RSS 条目抓取、正文提取。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

# 让 scripts/ 可导入
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import rss_utils

SAMPLE_OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>RSS</title></head>
  <body>
    <outline text="源A" xmlUrl="https://a.com/feed"/>
    <outline text="源B" xmlUrl="https://b.com/feed"/>
    <outline text="无URL源" />
    <outline title="源C" xmlUrl="https://c.com/feed"/>
  </body>
</opml>
"""

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>第一条</title>
    <link>https://a.com/1</link>
    <pubDate>Fri, 07 Aug 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>裸&amp;未转义源</title>
    <guid>https://a.com/2</guid>
    <pubDate>2026-08-07T11:00:00Z</pubDate>
  </item>
</channel></rss>
"""

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom 条目</title>
    <link href="https://x.com/1"/>
    <updated>2026-08-07T12:00:00+08:00</updated>
  </entry>
</feed>
"""


class TestLocalTag(unittest.TestCase):
    def test_strips_namespace(self):
        self.assertEqual(rss_utils.local_tag("{http://www.w3.org/2005/Atom}title"), "title")

    def test_plain_tag(self):
        self.assertEqual(rss_utils.local_tag("title"), "title")

    def test_empty(self):
        self.assertEqual(rss_utils.local_tag(""), "")


class TestParseTs(unittest.TestCase):
    def test_rfc822(self):
        ts = rss_utils.parse_ts("Fri, 07 Aug 2026 10:00:00 GMT")
        import datetime
        self.assertEqual(ts, datetime.datetime(2026, 8, 7, 10, 0, 0, tzinfo=datetime.timezone.utc).timestamp())

    def test_iso8601_z(self):
        import datetime
        ts = rss_utils.parse_ts("2026-08-07T10:00:00Z")
        self.assertEqual(ts, datetime.datetime(2026, 8, 7, 10, 0, 0, tzinfo=datetime.timezone.utc).timestamp())

    def test_iso8601_offset(self):
        import datetime
        ts = rss_utils.parse_ts("2026-08-07T12:00:00+08:00")
        self.assertEqual(ts, datetime.datetime(2026, 8, 7, 4, 0, 0, tzinfo=datetime.timezone.utc).timestamp())

    def test_bad(self):
        self.assertEqual(rss_utils.parse_ts("garbage"), 0)

    def test_empty(self):
        self.assertEqual(rss_utils.parse_ts(""), 0)
        self.assertEqual(rss_utils.parse_ts(None), 0)


class TestLoadFeeds(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.opml = os.path.join(self._tmp, "feeds.opml")
        with open(self.opml, "w", encoding="utf-8") as f:
            f.write(SAMPLE_OPML)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_all_enabled(self):
        feeds, disabled, only = rss_utils.load_feeds(self.opml)
        self.assertEqual(len(feeds), 3)
        self.assertEqual(feeds[0], {"name": "源A", "url": "https://a.com/feed"})
        self.assertEqual(disabled, [])
        self.assertEqual(only, "")

    def test_prefs_disable(self):
        prefs = os.path.join(self._tmp, "prefs.json")
        with open(prefs, "w", encoding="utf-8") as f:
            f.write('{"https://b.com/feed": false}')
        feeds, disabled, _ = rss_utils.load_feeds(self.opml, prefs_path=prefs)
        self.assertEqual(len(feeds), 2)
        self.assertIn("源B", disabled)

    def test_only_filter(self):
        feeds, disabled, only = rss_utils.load_feeds(self.opml, only_filter="https://b.com/feed")
        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0]["url"], "https://b.com/feed")
        self.assertEqual(only, "https://b.com/feed")

    def test_missing_opml(self):
        feeds, disabled, _ = rss_utils.load_feeds(os.path.join(self._tmp, "nope.opml"))
        self.assertEqual(feeds, [])


class FakeResp:
    def __init__(self, data, encoding=None):
        self._data = data
        self._encoding = encoding
        self.headers = {"Content-Encoding": encoding or ""}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        if n is None:
            return self._data
        return self._data[:n]


def _fake_urlopen(xml_bytes):
    def _f(req, timeout=15):
        return FakeResp(xml_bytes)
    return _f


class TestFetchFeedItems(unittest.TestCase):
    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(RSS_XML.encode("utf-8")))
    def test_rss_items(self, m):
        items = rss_utils.fetch_feed_items({"name": "A", "url": "https://a.com/feed"}, limit=5)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "第一条")
        self.assertEqual(items[0]["url"], "https://a.com/1")
        self.assertGreater(items[0]["ts"], 0)

    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(RSS_XML.encode("utf-8")))
    def test_guid_fallback_and_amp(self, m):
        # 第二条无 <link>，应回退用 <guid>；且原始内容带裸 & 需修复
        items = rss_utils.fetch_feed_items({"name": "A", "url": "https://a.com/feed"}, limit=5)
        self.assertEqual(items[1]["title"], "裸&未转义源")
        self.assertEqual(items[1]["url"], "https://a.com/2")

    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(ATOM_XML.encode("utf-8")))
    def test_atom_items(self, m):
        items = rss_utils.fetch_feed_items({"name": "X", "url": "https://x.com/feed"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Atom 条目")
        self.assertEqual(items[0]["url"], "https://x.com/1")
        self.assertGreater(items[0]["ts"], 0)

    @mock.patch("urllib.request.urlopen", side_effect=OSError("network down"))
    def test_network_error(self, m):
        items = rss_utils.fetch_feed_items({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(items, [])

    ENTITY_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item>
        <title>Apple&#39;s &quot;New&quot; Product &amp; Review</title>
        <link>https://a.com/1</link>
      </item>
    </channel></rss>
    """

    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(ENTITY_XML.encode("utf-8")))
    def test_html_entity_decode(self, m):
        """标题中的 HTML 实体应被解码为正常字符。"""
        items = rss_utils.fetch_feed_items({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(items[0]["title"], "Apple's \"New\" Product & Review")

    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(b"not xml at all"))
    def test_parse_error(self, m):
        items = rss_utils.fetch_feed_items({"name": "A", "url": "https://a.com/feed"})
        self.assertEqual(items, [])


class TestFetchArticleText(unittest.TestCase):
    HTML = "<html><head><title>示例标题</title></head><body><script>var x=1;</script><p>正文段落</p><p>更多内容</p></body></html>".encode("utf-8")

    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(HTML))
    def test_extract(self, m):
        text, err = rss_utils.fetch_article_text("https://x.com/a")
        self.assertIsNone(err)
        self.assertIn("标题: 示例标题", text)
        self.assertIn("正文段落", text)
        self.assertNotIn("var x=1", text)

    HTML_WITH_NAV = ("<html><head><title>标题</title></head><body>"
                     "<nav>导航导航</nav>"
                     "<article><p>主文正文第一段</p><p>主文正文第二段</p></article>"
                     "<footer>页脚版权</footer></body></html>").encode("utf-8")

    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(HTML_WITH_NAV))
    def test_extract_strips_nav_and_uses_article(self, m):
        text, err = rss_utils.fetch_article_text("https://x.com/a")
        self.assertIsNone(err)
        self.assertIn("主文正文第一段", text)
        self.assertIn("主文正文第二段", text)
        self.assertNotIn("导航导航", text)
        self.assertNotIn("页脚版权", text)

    @mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(b"<html><title>Anti-Bot</title><body>verify you are human to continue</body></html>"))
    def test_anti_bot(self, m):
        text, err = rss_utils.fetch_article_text("https://x.com/a")
        self.assertIsNone(text)
        self.assertEqual(err, "反爬拦截")

    @mock.patch("urllib.request.urlopen", side_effect=OSError("timeout"))
    def test_fetch_error(self, m):
        text, err = rss_utils.fetch_article_text("https://x.com/a")
        self.assertIsNone(text)
        self.assertIn("timeout", err)


if __name__ == "__main__":
    unittest.main()
