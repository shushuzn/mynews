#!/usr/bin/env python3
"""RSS 源健康检查：扫描 rss_sources.opml 中所有源，报告失效/异常的源。

用法:
  python scripts/check_rss_health.py                 # 全部检查，输出摘要
  python scripts/check_rss_health.py --verbose        # 输出每个源的状态
  python scripts/check_rss_health.py --failed-only    # 只输出有问题的源

检测项：HTTP 状态码、响应时间、内容是否可解析为 XML、是否含条目。
"""
import os
import sys
import time
import argparse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rss_utils
from rss_utils import _read_http, local_tag
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPML_PATH = os.path.join(BASE_DIR, "rss_sources.opml")


def check_one(feed: dict) -> dict:
    """检查单个源，返回状态字典。"""
    result = {"name": feed["name"], "url": feed["url"]}
    t0 = time.time()
    try:
        raw = _read_http(feed["url"], timeout=12)
        result["elapsed"] = round(time.time() - t0, 2)
        result["size"] = len(raw)
        try:
            root = ET.fromstring(raw.decode("utf-8", errors="replace"))
        except ET.ParseError as e:
            result["status"] = "parse_error"
            result["detail"] = str(e)[:100]
            return result
        # 统计条目数
        n_items = sum(1 for c in root.iter() if local_tag(c.tag) in ("item", "entry"))
        result["status"] = "ok"
        result["items"] = n_items
        if n_items == 0:
            result["status"] = "empty"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = f"{type(e).__name__}: {str(e)[:120]}"
    return result


def main():
    parser = argparse.ArgumentParser(description="RSS 源健康检查")
    parser.add_argument("--verbose", action="store_true", help="输出每个源的状态")
    parser.add_argument("--failed-only", action="store_true", help="只输出有问题的源")
    parser.add_argument("--limit", type=int, default=0, help="最多检查多少源（0=全部）")
    args = parser.parse_args()

    feeds, _, _ = rss_utils.load_feeds(OPML_PATH)
    if args.limit > 0:
        feeds = feeds[:args.limit]
    print(f"[health] 开始检查 {len(feeds)} 个 RSS 源...")

    results = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for r in pool.map(check_one, feeds):
            results.append(r)

    ok = [r for r in results if r["status"] == "ok"]
    empty = [r for r in results if r["status"] == "empty"]
    parse_err = [r for r in results if r["status"] == "parse_error"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"\n[health] 汇总: 正常 {len(ok)} · 空源 {len(empty)} · 解析失败 {len(parse_err)} · 网络错误 {len(errors)} / 共 {len(results)}")

    if args.failed_only or args.verbose:
        for r in results:
            if args.failed_only and r["status"] == "ok":
                continue
            if r["status"] == "ok":
                print(f"  [ok]     {r['name']} ({r['items']} 条, {r['elapsed']}s)")
            elif r["status"] == "empty":
                print(f"  [empty]  {r['name']} — {r['url']}")
            elif r["status"] == "parse_error":
                print(f"  [parse]  {r['name']} — {r['detail']} — {r['url']}")
            else:
                print(f"  [error]  {r['name']} — {r['detail']} — {r['url']}")

    if errors:
        print(f"\n[health] 建议：检查以下 {len(errors)} 个失效源，可在 Web UI 禁用或从 OPML 移除")
    return 1 if errors or parse_err else 0


if __name__ == "__main__":
    sys.exit(main())
