#!/usr/bin/env python3
"""
mynews 跨平台工具模块
提供项目根目录定位、OPENCODE 二进制路径查找、跨平台文件锁等公共能力。
Linux / Windows 通用，不依赖 fcntl/portalocker。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path


def setup_windows_utf8():
    """Windows 下将 stdout/stderr 切换为 utf-8，避免打印 Unicode 时崩溃。"""
    if sys.platform == "win32":
        import io
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", line_buffering=True
            )
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", line_buffering=True
            )


def get_base_dir() -> Path:
    """返回项目根目录（scripts/ 的父目录）。"""
    return Path(__file__).resolve().parent.parent


def get_temp_dir() -> Path:
    """返回系统临时目录。"""
    return Path(tempfile.gettempdir())


def get_opencode_bin() -> str:
    """
    查找 kimi 可执行文件路径。
    优先级：
    1. 环境变量 OPENCODE_BIN
    2. ~/.kimi-code/bin/kimi
    3. PATH 中的 kimi
    """
    env_bin = os.environ.get("OPENCODE_BIN")
    if env_bin:
        return env_bin

    home = Path.home()
    candidates = [
        home / ".kimi-code" / "bin" / "kimi",
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    for name in ("kimi", "kimi-code"):
        found = shutil.which(name)
        if found:
            return found

    # 兜底
    return "/root/.kimi-code/bin/kimi"


def _is_process_alive(pid: int) -> bool:
    """检查 PID 是否存活。Windows 与 Unix 均可用 os.kill(pid, 0)。"""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


class CrossPlatformLock:
    """
    跨平台单实例锁。
    通过写入当前 PID 的锁文件实现；如果锁文件存在且对应进程仍存活，则获取锁失败。
    非阻塞模式：获取失败直接抛出 BlockingIOError。
    阻塞模式：循环等待直到获取成功。
    """

    def __init__(self, lock_path: Path):
        self.lock_path = Path(lock_path)
        self._owned = False

    def _stale(self) -> bool:
        if not self.lock_path.exists():
            return True
        try:
            pid = int(self.lock_path.read_text(encoding="utf-8").strip())
        except Exception:
            # 文件内容损坏，视为过期
            return True
        return not _is_process_alive(pid)

    def acquire(self, blocking: bool = True):
        while True:
            if self._stale():
                try:
                    self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
                    self._owned = True
                    return
                except FileExistsError:
                    # 并发创建，重试
                    pass
            if not blocking:
                raise BlockingIOError(f"Lock already held: {self.lock_path}")
            import time
            time.sleep(0.2)

    def release(self):
        if self._owned and self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
            finally:
                self._owned = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


# ==================== 微信公众号抓取 ====================

import time
import json
import re
import hashlib

# iPhone UA
IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

# PC UA
PC_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 备用 API（微信公众号文章中转服务）
BACKUP_APIS = [
    "https://www.newureader.com/",
    "https://rss.aiown.cn/api/wxarticle?url=",
    "https://api.pfeng.cn/wx/article?url=",
    "https://wxb.sangzhuaya.com/api/wx?url=",
]

# Android UA（部分文章 iPhone UA 受限，Android UA 可用）
ANDROID_UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"


def is_wechat_url(url: str) -> bool:
    """判断是否为微信公众号 URL"""
    return url and "mp.weixin.qq.com" in url


def get_cache_dir() -> Path:
    """获取缓存目录"""
    cache_dir = get_temp_dir() / "mynews_wx_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cache_path(url: str) -> Path:
    """根据 URL 生成缓存文件路径"""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return get_cache_dir() / f"{url_hash}.json"


def fetch_with_retry(url: str, headers: dict, timeout: int = 15, max_retries: int = 3) -> tuple:
    """
    带重试的 HTTP 请求
    返回: (content, error_msg)
    """
    import urllib.request

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="replace"), None
                else:
                    return None, f"HTTP {resp.status}"
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避
            else:
                return None, str(e)
    return None, "max retries exceeded"


def extract_wechat_content(html: str) -> str:
    """从微信公众号 HTML 中提取正文"""
    import html as html_module

    # 方法1: 提取 id=js_content（最可靠）
    match = re.search(r'<div[^>]*\sid=["\']js_content["\'][^>]*>(.*)', html, re.DOTALL)
    if match:
        # 从匹配点开始，找到下一个 </div>
        rest = match.group(1)
        # 找到第一个 </div> 之前的内容
        end_idx = rest.find('</div>')
        if end_idx > 0:
            content_html = rest[:end_idx]
        else:
            content_html = rest

        # 清理 HTML
        content_html = _clean_html_content(content_html)
        if len(content_html) > 100:
            return html_module.unescape(content_html)

    # 方法2: 提取 rich_media_content
    match = re.search(r'<div[^>]*class=["\'][^"\']*rich_media_content[^"\']*["\'][^>]*>(.*?)</div>', html, re.DOTALL)
    if match:
        content_html = _clean_html_content(match.group(1))
        if len(content_html) > 100:
            return html_module.unescape(content_html)

    # 方法3: 提取 img-content 容器
    match = re.search(r'<div[^>]*id=["\']img-content["\'][^>]*>(.*?)</div>', html, re.DOTALL)
    if match:
        content_html = _clean_html_content(match.group(1))
        if len(content_html) > 100:
            return html_module.unescape(content_html)

    # 方法4: 提取 section 标签内的正文（新版微信文章）
    match = re.search(r'<section[^>]*class=["\'][^"\']*article-content[^"\']*["\'][^>]*>(.*?)</section>', html, re.DOTALL)
    if match:
        content_html = _clean_html_content(match.group(1))
        if len(content_html) > 100:
            return html_module.unescape(content_html)

    # 方法5: 提取 <article> 标签（部分文章使用）
    match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if match:
        content_html = _clean_html_content(match.group(1))
        if len(content_html) > 100:
            return html_module.unescape(content_html)

    return ""


def _clean_html_content(html_content: str) -> str:
    """清理 HTML 内容，移除脚本、样式、图片等"""
    import re

    # 移除脚本和样式
    html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)
    # 移除图片
    html_content = re.sub(r'<img[^>]*>', '', html_content)
    # 移除视频
    html_content = re.sub(r'<video[^>]*>.*?</video>', '', html_content, flags=re.DOTALL)
    # 转换 HTML 标签为纯文本
    html_content = re.sub(r'<br\s*/?>', '\n', html_content)
    html_content = re.sub(r'</p>', '\n\n', html_content)
    html_content = re.sub(r'<p[^>]*>', '', html_content)
    html_content = re.sub(r'<section[^>]*>', '\n', html_content)
    html_content = re.sub(r'</section>', '\n', html_content)
    html_content = re.sub(r'<[^>]+>', '', html_content)
    # 清理多余空白
    html_content = re.sub(r'\n{3,}', '\n\n', html_content)
    html_content = html_content.strip()

    return html_content


def extract_title_from_html(html: str) -> str:
    """从微信公众号 HTML 中提取文章标题。"""
    # 优先从 <title> 提取
    match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if match:
        title = match.group(1).strip()
        # 清理常见后缀
        title = re.sub(r'[_-]微信.*$', '', title)
        title = re.sub(r'\s*-\s*.*$', '', title)
        if title:
            return title
    # 次选从 og:title 提取
    match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def extract_author_from_html(html: str) -> str:
    """从微信公众号 HTML 中提取发布账号（作者）。"""
    # 优先从 <meta name="author"> 提取
    match = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        author = match.group(1).strip()
        if author:
            return author
    # 次选从 og:site_name 提取
    match = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def fetch_wechat_article(url: str, use_cache: bool = True) -> tuple:
    """
    抓取微信公众号文章，支持多重降级策略

    策略顺序:
    1. 读取缓存
    2. iPhone UA + 标准解析
    3. PC UA + 标准解析
    4. Android UA
    5. 备用 API 中转

    返回: (content, source_info, error_msg, article_title)
    """
    # 检查缓存
    cache_path = get_cache_path(url)
    if use_cache and cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
                if cached.get("content") and cached.get("content") != "FETCH_FAILED":
                    return cached["content"], cached.get("source", ""), "cache", cached.get("title", "")
        except Exception:
            pass

    raw_html = None  # 保存原始 HTML 以便提取标题

    # 策略1: iPhone UA（最常用，成功率最高）
    print(f"    [wechat] 尝试 iPhone UA...")
    headers = {"User-Agent": IPHONE_UA}
    content, error = fetch_with_retry(url, headers, timeout=20, max_retries=4)
    if content:
        raw_html = content
        text = extract_wechat_content(content)
        if text and len(text) > 100:
            title = extract_title_from_html(content)
            author = extract_author_from_html(content)
            source = author if author else "iPhone UA"
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"content": text, "source": source, "url": url, "title": title}, f)
            return text, source, None, title

    # 策略2: PC UA
    print(f"    [wechat] 尝试 PC UA...")
    headers = {"User-Agent": PC_UA}
    content, error = fetch_with_retry(url, headers, timeout=20, max_retries=4)
    if content:
        raw_html = content
        text = extract_wechat_content(content)
        if text and len(text) > 100:
            title = extract_title_from_html(content)
            author = extract_author_from_html(content)
            source = author if author else "PC UA"
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"content": text, "source": source, "url": url, "title": title}, f)
            return text, source, None, title

    # 策略3: Android UA
    print(f"    [wechat] 尝试 Android UA...")
    headers = {"User-Agent": ANDROID_UA}
    content, error = fetch_with_retry(url, headers, timeout=20, max_retries=3)
    if content:
        raw_html = content
        text = extract_wechat_content(content)
        if text and len(text) > 100:
            title = extract_title_from_html(content)
            author = extract_author_from_html(content)
            source = author if author else "Android UA"
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"content": text, "source": source, "url": url, "title": title}, f)
            return text, source, None, title

    # 策略4: 备用 API 中转
    for api_base in BACKUP_APIS:
        print(f"    [wechat] 尝试备用 API: {api_base}")
        try:
            api_url = f"{api_base}{url}"
            headers = {"User-Agent": PC_UA}
            content, error = fetch_with_retry(api_url, headers, timeout=20)
            if content:
                # 备用 API 可能直接返回文本或 JSON
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        text = data.get("content", "") or data.get("text", "") or data.get("data", "")
                        if text:
                            title = data.get("title", "") or (extract_title_from_html(text) if not isinstance(text, str) else "")
                            with open(cache_path, "w", encoding="utf-8") as f:
                                json.dump({"content": text, "source": api_base, "url": url, "title": title}, f)
                            return text, api_base, None, title
                except json.JSONDecodeError:
                    # 直接返回文本
                    raw_html = content
                    text = extract_wechat_content(content)
                    if text and len(text) > 100:
                        title = extract_title_from_html(content)
                        author = extract_author_from_html(content)
                        source = author if author else api_base
                        with open(cache_path, "w", encoding="utf-8") as f:
                            json.dump({"content": text, "source": source, "url": url, "title": title}, f)
                        return text, source, None, title
        except Exception as e:
            print(f"    [wechat] API {api_base} 失败: {e}")
            continue

    # 全部失败，保存失败标记
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"content": "FETCH_FAILED", "source": "failed", "url": url, "error": str(error)}, f)

    return "", "", f"全部策略失败: {error}", ""


def clear_cache(url: str = None):
    """清除缓存，可指定单个 URL 或全部清除"""
    cache_dir = get_cache_dir()
    if url:
        cache_path = get_cache_path(url)
        if cache_path.exists():
            cache_path.unlink()
            print(f"已清除缓存: {url}")
    else:
        for f in cache_dir.glob("*.json"):
            f.unlink()
        print(f"已清除全部 {len(list(cache_dir.glob('*.json')))} 个缓存文件")
