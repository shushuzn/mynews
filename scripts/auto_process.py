#!/usr/bin/env python3
"""
非交互式自动处理器
从 OPML RSS 源循环抓取最新条目，自动处理直到全部完成。
用法: python auto_process.py [--limit N] [--force] [--delay S]
"""
import os, sys, json, subprocess, time, re, tempfile, argparse, threading
from pathlib import Path
import xml.etree.ElementTree as ET

# 共享 RSS / 网页抓取工具（rss_utils.py 与 server.py 同目录/被 webui 复用）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rss_utils import local_tag, parse_ts, fetch_feed_items, fetch_article_text

# Windows 下禁止子进程弹出控制台窗口（避免终端闪现）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# ---- 路径配置 ----
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
OPML_PATH = BASE_DIR / "rss_sources.opml"
PROCESSOR = SCRIPTS_DIR / "process_inbox.py"

# ---- flomo token ----
FLOMO_TOKEN = os.environ.get("FLOMO_TOKEN")
if not FLOMO_TOKEN:
    env_file = BASE_DIR / ".flomo_env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("FLOMO_TOKEN="):
                FLOMO_TOKEN = line.split("=", 1)[1].strip().strip("\"'")
                break
if not FLOMO_TOKEN:
    print("[error] FLOMO_TOKEN 未设置")
    sys.exit(1)

# ---- 已处理记录 ----
RECORD_FILE = SCRIPTS_DIR / ".auto_processed.json"
RECORD_LOCK = threading.Lock()
# 记录容量上限：只保留最近 PROCESSED_MAX 条 URL，防止文件无限膨胀
# （前端 hn-processed 上限 600，这里给服务端更大的缓冲但仍有界）
PROCESSED_MAX = 5000

def load_processed():
    with RECORD_LOCK:
        return _load_processed()

def _load_processed():
    if RECORD_FILE.exists():
        try:
            return set(json.loads(RECORD_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()

def save_processed(processed):
    with RECORD_LOCK:
        _save_processed(processed)

def _save_processed(processed):
    # 容量保护：超过上限时截断到最多 PROCESSED_MAX 条，防止文件无限膨胀。
    # 集合无序，截断不保证保留"最近"，但 mark_processed 单条追加时通常集合规模在上限内。
    items = list(processed)
    if len(items) > PROCESSED_MAX:
        items = items[-PROCESSED_MAX:]
    RECORD_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

def mark_processed(url, processed=None):
    """原子标记已处理：读文件 → add → 写回，避免并发覆盖丢失其他线程/进程的标记。"""
    with RECORD_LOCK:
        current = _load_processed()
        current.add(url)
        _save_processed(current)
    if processed is not None:
        processed.add(url)

# ---- RSS 抓取 ----

def fetch_all_rss_items(limit_per_feed=3):
    if not OPML_PATH.exists():
        print(f"[error] OPML 不存在: {OPML_PATH}")
        return []
    try:
        root = ET.parse(str(OPML_PATH)).getroot()
    except Exception as e:
        print(f"[error] OPML 解析失败: {e}")
        return []
    # 启用配置：scripts/.rss_feeds.json（{"url": bool}，缺省启用），与 Web UI 共用
    prefs = {}
    prefs_file = SCRIPTS_DIR / ".rss_feeds.json"
    try:
        if prefs_file.exists():
            prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    # 可选过滤：MYNEWS_RSS_ONLY="https://hnrss.org/newest" 时只抓取该源（精确匹配，不含关键词变体）
    only_filter = os.environ.get("MYNEWS_RSS_ONLY", "").strip()
    feeds = []
    disabled = []
    for o in root.iter("outline"):
        url = (o.get("xmlUrl") or "").strip()
        name = (o.get("text") or o.get("title") or "").strip()
        if not url:
            continue
        if only_filter and url != only_filter:
            continue
        if not prefs.get(url, True):
            disabled.append(name or url)
            continue
        feeds.append({"name": name or url, "url": url})
    print(f"[rss] 共 {len(feeds)} 个 RSS 源（过滤: {'无' if not only_filter else only_filter}，禁用: {len(disabled)}），并发抓取中...")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_items = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_feed_items, f, limit_per_feed): f for f in feeds}
        for fut in as_completed(futures):
            try:
                all_items.extend(fut.result())
            except Exception:
                pass
    # 去重 + 按时间倒序
    seen, merged = set(), []
    for it in all_items:
        if it["url"] not in seen:
            seen.add(it["url"])
            merged.append(it)
    merged.sort(key=lambda x: x.get("ts", 0), reverse=True)
    print(f"[rss] 获取 {len(merged)} 条条目")
    return merged

# ---- 抓取正文 ----
def fetch_article(url, timeout=15):
    """抓取网页正文（复用 rss_utils），返回 (content, err)。"""
    result, err = fetch_article_text(url, timeout=timeout)
    if result and not err:
        print(f"  [fetch] 抓取成功: {len(result)} 字符")
    return result, err

# ---- 运行处理 ----
def process_article(content, url):
    env = os.environ.copy()
    env["FLOMO_TOKEN"] = FLOMO_TOKEN
    cmd = [sys.executable, str(PROCESSOR), "--content", content, "--auto"]
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                          timeout=600, env=env, cwd=str(SCRIPTS_DIR), creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or "") + ("\n--- stderr ---\n" + r.stderr if r.stderr else "")
        success = any(k in out for k in ("上传成功", "更新成功", "处理完成", "无增量 → 跳过"))
        return success, out
    except subprocess.TimeoutExpired:
        return False, "处理超时（>10分钟）"
    except Exception as e:
        return False, f"执行错误: {e}"

def main():
    parser = argparse.ArgumentParser(description="mynews 非交互式自动处理器")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条（0=不限）")
    parser.add_argument("--force", action="store_true", help="强制处理全部条目（忽略已处理记录，默认跳过已处理）")
    parser.add_argument("--delay", type=int, default=3, help="处理间隔秒数（默认3）")
    args = parser.parse_args()

    processed = set() if args.force else load_processed()
    print(f"[start] 已跳过 {len(processed)} 条历史记录" if not args.force else "[start] --force 模式：忽略历史记录，处理全部")

    items = fetch_all_rss_items(limit_per_feed=3)
    print(f"[rss] 共获取 {len(items)} 条条目")

    total, success, skip, fail = 0, 0, 0, 0
    for it in items:
        url = it["url"]
        if url in processed:
            print(f"  [skip] 已处理: {url}")
            skip += 1
            continue

        print(f"\n[{total+1}] {it['source']}: {it['title'] or url}")
        print(f"  [url] {url}")

        content, err = fetch_article(url)
        if err:
            print(f"  [error] 抓取失败: {err}，标记跳过")
            mark_processed(url, processed)
            fail += 1
            continue

        ok, out = process_article(content, url)
        total += 1
        if ok:
            success += 1
            print(f"  [ok] 处理成功")
        else:
            fail += 1
            print(f"  [fail] 处理失败:\n{out[:500]}")

        mark_processed(url, processed)

        if args.limit > 0 and total >= args.limit:
            print(f"\n[done] 已达处理上限 {args.limit} 条")
            break
        if args.delay > 0 and total < len(items) - skip:
            time.sleep(args.delay)

    print(f"\n==== 汇总 ====")
    print(f"处理: {total} | 成功: {success} | 失败: {fail} | 历史跳过: {skip}")
    print(f"已处理记录已保存至: {RECORD_FILE}")

if __name__ == "__main__":
    main()
