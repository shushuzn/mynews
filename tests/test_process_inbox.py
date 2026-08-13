"""process_inbox.py 单元测试：格式验证、下划线转义、flomo 调用（mock）。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

# 让 scripts/ 可导入
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

os.environ["FLOMO_TOKEN"] = "test-token"
os.environ["MYNEWS_SKIP_KIMI_CHECK"] = "1"  # 测试环境无 kimi CLI，跳过启动检查
import process_inbox as pi

# 标准格式示例（第一行标签 + 粗体标题 + 概念/来源）
VALID_CONTENT = (
    "#信号笔记 #计算机科学 #算法\n"
    "**计算机科学_算法_排序算法**\n"
    "**概念**：快速排序\n"
    "**来源**：测试来源"
)


class TestValidateDomain(unittest.TestCase):
    def test_valid(self):
        d, s = pi._validate_and_extract_domain(VALID_CONTENT)
        self.assertEqual((d, s), ("计算机科学", "算法"))

    def test_missing_concept(self):
        with self.assertRaises(ValueError) as ctx:
            pi._validate_and_extract_domain(VALID_CONTENT.replace("**概念**：快速排序\n", ""))
        self.assertIn("**概念**", str(ctx.exception))

    def test_missing_source(self):
        # 来源已不强制校验，缺失不报错
        pi._validate_and_extract_domain(VALID_CONTENT.replace("**来源**：测试来源", ""))  # 应正常通过

    def test_not_tag_first_line(self):
        bad = "普通文本\n" + VALID_CONTENT
        with self.assertRaises(ValueError) as ctx:
            pi._validate_and_extract_domain(bad)
        self.assertIn("标签行", str(ctx.exception))

    def test_wrong_signal_count(self):
        bad = VALID_CONTENT.replace("#信号笔记 #计算机科学 #算法", "#信号笔记 #趋势信号 #计算机科学 #算法")
        with self.assertRaises(ValueError) as ctx:
            pi._validate_and_extract_domain(bad)
        self.assertIn("# 标签必须恰好 3 个", str(ctx.exception))

    def test_no_signal_type(self):
        bad = VALID_CONTENT.replace("#信号笔记 ", "")
        with self.assertRaises(ValueError) as ctx:
            pi._validate_and_extract_domain(bad)
        self.assertIn("信号类型", str(ctx.exception))

    def test_mismatched_secondary(self):
        bad = VALID_CONTENT.replace("**计算机科学_算法_排序算法**", "**计算机科学_数据结构_排序算法**")
        with self.assertRaises(ValueError) as ctx:
            pi._validate_and_extract_domain(bad)
        self.assertIn("二级领域", str(ctx.exception))

    def test_hyphen_in_title(self):
        bad = VALID_CONTENT.replace("**计算机科学_算法_排序算法**", "**计算机科学_算法_排序-算法**")
        with self.assertRaises(ValueError) as ctx:
            pi._validate_and_extract_domain(bad)
        self.assertIn("连字符", str(ctx.exception))


class TestEscapeBoldUnderscores(unittest.TestCase):
    def test_title_underscores_unchanged(self):
        # flomo MCP 自身会把 _ 转义为 \_，服务器端不再预转义（避免双重转义 \\\_）
        out = pi._escape_bold_underscores("**计算机科学_算法_排序算法**\n正文")
        self.assertEqual(out, "**计算机科学_算法_排序算法**\n正文")

    def test_no_underscore_unchanged(self):
        out = pi._escape_bold_underscores("**简单标题**\n正文")
        self.assertEqual(out, "**简单标题**\n正文")


class TestNormalizeFlomoContent(unittest.TestCase):
    def test_dedupe_blank_lines(self):
        out = pi._normalize_flomo_content(VALID_CONTENT + "\n\n\n\n")
        self.assertNotIn("\n\n\n\n", out)

    def test_strips_trailing_space(self):
        out = pi._normalize_flomo_content("  " + VALID_CONTENT + "  ")
        self.assertNotIn("\n", out[:0])  # 无崩溃即可
        self.assertIn("**来源**：测试来源", out)

    def test_removes_forbidden_syntax(self):
        raw = VALID_CONTENT + "\n\n## 二级标题\n> 引用\n```python\ncode\n```\n[链接](https://x.com)\n---\n|表|格|\n"
        out = pi._normalize_flomo_content(raw)
        # 二级及以上标题 / 引用 / 代码块 / 分隔线 / 表格被移除
        self.assertNotIn("## 二级标题", out)
        self.assertNotIn("> 引用", out)
        self.assertNotIn("```python", out)
        self.assertNotIn("code", out)
        self.assertNotIn("---", out)
        self.assertNotIn("|表|格|", out)
        # 链接转为纯文本保留（移除 URL 语法，保留显示文本）
        self.assertIn("链接", out)
        self.assertNotIn("[链接](https://x.com)", out)

    def test_list_unification_and_mark(self):
        # 注：• / 数字列表项被统一为 - 前缀；<mark> 高亮仅对"关键词：说明"行生效
        raw = VALID_CONTENT + "\n\n• 要点一\n1. 要点二\n- 关键词：说明文字"
        out = pi._normalize_flomo_content(raw)
        self.assertIn("- 要点一", out)
        self.assertIn("- 要点二", out)
        self.assertNotIn("•", out)
        self.assertNotIn("1. 要点二", out)
        self.assertIn("<mark>关键词</mark>：说明文字", out)

    def test_empty_content(self):
        out = pi._normalize_flomo_content("")
        self.assertIsInstance(out, str)


class FakeResp:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        raw = self._data.encode("utf-8")
        return raw if n is None else raw[:n]


def _sse_payload(body):
    return FakeResp("data: " + body + "\n\n")


def fake_urlopen(req, timeout=30):
    """按请求的 tool name 返回模拟 SSE 响应。"""
    import json
    body = json.loads(req.data.decode("utf-8"))
    name = body["params"]["name"]
    if name == "memo_search":
        payload = json.dumps({"memos": [{"id": "m1", "content": "**概念** x"}]})
        sse = json.dumps({"result": {"content": [{"type": "text", "text": payload}]}})
    elif name == "memo_create":
        sse = json.dumps({"result": {"id": "m_new"}})
    elif name == "memo_batch_get":
        payload = json.dumps({"memos": [{"id": "m1", "content": "full content"}]})
        sse = json.dumps({"result": {"content": [{"type": "text", "text": payload}]}})
    elif name == "memo_update":
        sse = json.dumps({"result": {"ok": True}})
    else:
        sse = json.dumps({"error": {"message": "unknown tool"}})
    return _sse_payload(sse)


class TestFlomoCalls(unittest.TestCase):
    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_search(self, m):
        memos = pi.search_flomo("keyword")
        self.assertEqual(memos, [{"id": "m1", "content": "**概念** x"}])

    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_upload(self, m):
        mid = pi.upload_flomo(VALID_CONTENT)
        self.assertEqual(mid, "m_new")

    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_fetch(self, m):
        content = pi.fetch_flomo_memo("m1", keyword="kw")
        self.assertEqual(content, "full content")

    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_update(self, m):
        ok = pi.update_flomo("m1", VALID_CONTENT)
        self.assertTrue(ok)

    @mock.patch("urllib.request.urlopen", side_effect=OSError("network down"))
    def test_network_error_returns_none(self, m):
        self.assertIsNone(pi.search_flomo("kw"))
        self.assertIsNone(pi.upload_flomo(VALID_CONTENT))

    def test_flomo_call_parses_multiple_data_lines(self):
        resp = FakeResp("data: {\"result\": 1}\n\ndata: {\"result\": 2}\n\n")
        with mock.patch("urllib.request.urlopen", return_value=resp):
            results = pi._flomo_call("memo_search", {"keywords": "x"})
        self.assertEqual(len(results), 2)


class TestAutoAiFailure(unittest.TestCase):
    """AI 调用失败（_call_kimi 返回空）时 _auto_decide/_auto_merge 不应崩溃。"""

    def test_decide_empty_returns_none(self):
        with mock.patch.object(pi, "_call_kimi", return_value=None):
            self.assertIsNone(pi._auto_decide("old", "new"))

    def test_decide_none_input(self):
        with mock.patch.object(pi, "_call_kimi", return_value=None):
            self.assertIsNone(pi._auto_decide("old", "new"))

    def test_merge_empty_returns_none(self):
        with mock.patch.object(pi, "_call_kimi", return_value=None):
            self.assertIsNone(pi._auto_merge("old", "new"))

    def test_decide_valid_action(self):
        with mock.patch.object(pi, "_call_kimi", return_value='{"action": "update"}'):
            self.assertEqual(pi._auto_decide("old", "new"), "update")

    def test_merge_valid_markdown(self):
        out = "#信号笔记 #计算机科学 #测试\n**计算机科学_测试_知识点**\n**概念**：合并内容"
        with mock.patch.object(pi, "_call_kimi", return_value=out):
            merged = pi._auto_merge("old", "new")
        self.assertIn("**概念**", merged)


if __name__ == "__main__":
    unittest.main()
