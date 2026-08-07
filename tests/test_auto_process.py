"""auto_process.py 单元测试：已处理记录容量截断、集合同步。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import json
import tempfile
import unittest
import pathlib

# 让 scripts/ 可导入
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

os.environ["FLOMO_TOKEN"] = "test-token"
os.environ["MYNEWS_SKIP_KIMI_CHECK"] = "1"  # 测试环境无 kimi CLI，跳过启动检查
import auto_process as ap


class TestProcessedRecords(unittest.TestCase):
    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        self._old_file = ap.RECORD_FILE
        self._old_max = ap.PROCESSED_MAX
        ap.RECORD_FILE = self._tmp / "processed.json"

    def tearDown(self):
        ap.RECORD_FILE = self._old_file
        ap.PROCESSED_MAX = self._old_max
        import shutil
        shutil.rmtree(str(self._tmp), ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        ap.save_processed({"url1", "url2"})
        loaded = ap.load_processed()
        self.assertEqual(loaded, {"url1", "url2"})

    def test_capacity_trim(self):
        ap.PROCESSED_MAX = 10
        ap.save_processed(set(f"url{i}" for i in range(100)))
        loaded = ap.load_processed()
        self.assertLessEqual(len(loaded), 10)

    def test_mark_processed_syncs_local_set(self):
        local = set()
        ap.mark_processed("newnode", local)
        self.assertIn("newnode", local)
        self.assertIn("newnode", ap.load_processed())

    def test_mark_processed_persists(self):
        ap.mark_processed("abc")
        self.assertIn("abc", ap.load_processed())

    def test_missing_file_returns_empty(self):
        self.assertEqual(ap.load_processed(), set())

    def test_corrupt_file_returns_empty(self):
        (self._tmp / "processed.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(ap.load_processed(), set())


class TestRecordFileConstant(unittest.TestCase):
    def test_max_defined(self):
        self.assertIsInstance(ap.PROCESSED_MAX, int)
        self.assertGreater(ap.PROCESSED_MAX, 0)


if __name__ == "__main__":
    unittest.main()
