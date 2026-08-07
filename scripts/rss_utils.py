#!/usr/bin/env python3
"""
RSS / 网页抓取共享工具（供 auto_process.py 与 webui/server.py 复用）。
集中了 RSS 解析、时间戳解析、单源条目抓取、网页正文提取的逻辑，消除两份重复实现。
"""
import re
import datetime
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
ANTI_PATTERNS = ('环境异常', 'captcha', 'verify you are human', 'access denied')


def local_tag(tag: str) -> str:
    """去掉 XML 命名空间前缀（如 {http://...}title → title）。"""
    return tag.split("}")[-1]


def parse_ts(text: str) -> float:
    """解析 RSS pubDate（RFC822）或 Atom published/updated（ISO8601）为时间戳，失败返回 0。"""
    if not text:
        return 0
    try:
        return parsedate_to_datetime(text.strip()).timestamp()
    except Exception:
        pass
    try:
        s = text.strip().replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0


def load_feeds(opml_path, prefs_path=None, only_filter=""):
    """解析 OPML 并应用启用配置/硬过滤，返回 [{name, url}]。

    - prefs_path：scripts/.rss_feeds.json（{"url": bool}，缺省启用）；None 或文件缺失 = 全部启用
    - only_filter：MYNEWS_RSS_ONLY 硬过滤，非空时仅保留 URL 精确匹配的源
    - 解析失败或 OPML 不存在返回 []
    """
    import os as _os
    import json as _json
    try:
        if not _os.path.exists(str(opml_path)):
            return [], [], (only_filter or "").strip()
        root = ET.parse(str(opml_path)).getroot()
    except Exception:
        return [], [], (only_filter or "").strip()
    prefs = {}
    if prefs_path is not None:
        try:
            if _os.path.exists(str(prefs_path)):
                with open(str(prefs_path), encoding="utf-8") as f:
                    prefs = _json.loads(f.read())
        except Exception:
            pass
    only_filter = (only_filter or "").strip()
    feeds, disabled = [], []
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
    return feeds, disabled, only_filter


def _read_http(url: str, timeout: int = 15) -> bytes:
    """GET 请求并自动解压 gzip，返回原始字节。失败抛异常由调用方处理。"""
    import urllib.request as _ur
    import gzip as _gz
    req = _ur.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with _ur.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(10_000_000)
        if (resp.headers.get("Content-Encoding", "") or "").lower() == "gzip":
            try:
                raw = _gz.decompress(raw)
            except Exception:
                pass
        return raw


def fetch_feed_items(feed: dict, limit: int = 2) -> list:
    """抓取单个 RSS/Atom 源的最新条目，返回 [{title, url, source, ts}]。

    健壮性（来自 webui/server.py 的累积修复）：
    - gzip 压缩响应
    - 源侧未转义的裸 & 修复
    - 无 <link> 元素时回退用 <guid> 作为 URL
    """
    try:
        xml = _read_http(feed["url"], timeout=15).decode("utf-8", errors="replace")
        # 防御性清理：修复源侧未转义的裸 &（如 爱范儿 feed 中 &</image>）
        if "&" in xml:
            xml = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", xml)
        root = ET.fromstring(xml)
        items = []
        for child in root.iter():
            if local_tag(child.tag) not in ("item", "entry"):
                continue
            title = link = ts_text = ""
            for sub in child:
                ln = local_tag(sub.tag)
                if ln == "title":
                    title = (sub.text or "").strip()
                elif ln == "link":
                    href = sub.get("href")
                    if href:
                        link = href.strip()
                    elif sub.text:
                        link = sub.text.strip()
                elif ln == "guid" and not link:
                    # 部分源无 <link> 元素（如安全客），回退用 <guid> 作为 URL
                    link = (sub.text or "").strip()
                elif ln in ("pubDate", "published", "updated", "date"):
                    ts_text = (sub.text or "").strip()
            if title and link:
                items.append({
                    "title": title,
                    "url": link,
                    "source": feed["name"],
                    "ts": parse_ts(ts_text)
                })
            if len(items) >= limit:
                break
        return items
    except ET.ParseError as e:
        print(f"[rss] RSS 解析失败: {feed.get('name', '')} ({feed.get('url', '')}): {e}")
        return []
    except Exception:
        return []


def fetch_article_text(url: str, timeout: int = 15):
    """从 URL 抓取正文，返回 (text, err)。

    text: 提取后的正文（含"标题: xxx"前缀），反爬被拦截时 err 为 "反爬拦截"；
    成功时 err 为 None。失败时 text 为 None，err 为异常信息。
    """
    try:
        html = _read_http(url, timeout=timeout).decode("utf-8", errors="replace")
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        title = title_m.group(1).strip() if title_m else ""
        clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL)
        # 剥离常见导航/辅助区域，减少正文提取噪声（针对单篇正文抓取）
        clean = re.sub(
            r'<(nav|footer|aside)[^>]*>.*?</\1>',
            '', clean, flags=re.DOTALL | re.IGNORECASE
        )
        # 尝试优先提取 <article> / <main> 主区域（存在时用主区域，否则用全文）
        main_m = re.search(
            r'<(article|main)[^>]*>([\s\S]*?)</\1>', clean, re.IGNORECASE
        )
        if main_m:
            clean = main_m.group(2)
        clean = re.sub(r'<[^>]+>', '\n', clean)
        clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
        body = clean[:8000]
        result = (f"标题: {title}\n\n{body}" if title else body).strip()
        # 检测反爬
        for pat in ANTI_PATTERNS:
            if pat in body[:200].lower():
                return None, "反爬拦截"
        return result, None
    except Exception as e:
        return None, str(e)
