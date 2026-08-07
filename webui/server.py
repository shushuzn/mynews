#!/usr/bin/env python3
"""
mynews Web UI 服务器
启动: python3 server.py [端口]
前端: http://localhost:8080
"""
import os, sys, json, subprocess, threading, re, time, socket
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

# 共享 RSS / 网页抓取工具（scripts/rss_utils.py）
sys.path.insert(0, str(SCRIPTS_DIR))
from rss_utils import local_tag, parse_ts, fetch_feed_items, fetch_article_text

# 优先环境变量，其次 .env 文件
FLOMO_TOKEN = os.environ.get("FLOMO_TOKEN")
if not FLOMO_TOKEN:
    env_file = BASE_DIR / ".flomo_env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("FLOMO_TOKEN="):
                FLOMO_TOKEN = line.split("=", 1)[1].strip().strip("\"'")
                break

# RSS 源列表（OPML 文件路径，可用环境变量 OPML_PATH 覆盖）
OPML_PATH = Path(os.environ.get("OPML_PATH", str(BASE_DIR / "rss_sources.opml")))

# RSS 源启用配置（scripts/.rss_feeds.json）：{"url": true/false}，缺省全部启用；
# MYNEWS_RSS_ONLY 环境变量仍为硬过滤（精确匹配时只保留该源）
RSS_FEEDS_PREFS = SCRIPTS_DIR / ".rss_feeds.json"
RSS_PREFS_LOCK = threading.Lock()

def _load_feed_prefs() -> dict:
    """读取启用配置，返回 {url: bool}；文件不存在返回空（=全部启用）。"""
    try:
        if RSS_FEEDS_PREFS.exists():
            return json.loads(RSS_FEEDS_PREFS.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[server] RSS 配置解析失败: {e}")
    return {}

def _save_feed_prefs(prefs: dict):
    with RSS_PREFS_LOCK:
        RSS_FEEDS_PREFS.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")

def _feed_enabled(url: str, prefs: dict) -> bool:
    """URL 是否启用：配置中有记录按配置，无记录默认启用。"""
    return prefs.get(url, True)

def _feed_list() -> dict:
    """返回全部 RSS 源及启用状态：{feeds: [{name, url, enabled}], total, enabled_count}。"""
    try:
        if not OPML_PATH.exists():
            return {"feeds": [], "total": 0, "enabled_count": 0}
        root = ET.parse(str(OPML_PATH)).getroot()
        prefs = _load_feed_prefs()
        only_filter = os.environ.get("MYNEWS_RSS_ONLY", "").strip()
        feeds = []
        for o in root.iter("outline"):
            url = (o.get("xmlUrl") or "").strip()
            name = (o.get("text") or o.get("title") or "").strip()
            if not url:
                continue
            feeds.append({"name": name or url, "url": url,
                          "enabled": _feed_enabled(url, prefs),
                          "hard_locked": bool(only_filter) and url != only_filter})
        enabled_count = sum(1 for f in feeds if f["enabled"])
        return {"feeds": feeds, "total": len(feeds), "enabled_count": enabled_count}
    except Exception as e:
        print(f"[server] RSS 源列表失败: {e}")
        return {"feeds": [], "total": 0, "enabled_count": 0}

# 后台刷新间隔（秒），可用环境变量 MYNEWS_RSS_INTERVAL 覆盖
RSS_INTERVAL = max(30, int(os.environ.get("MYNEWS_RSS_INTERVAL", "180")))

# RSS 聚合缓存（首次全量抓取，之后 RSS_INTERVAL*2 内复用）
RSS_CACHE = {"ts": 0.0, "items": []}
RSS_LOCK = threading.Lock()

def _load_feeds() -> list:
    """解析 OPML，返回 [{name, url}]。优先级：MYNEWS_RSS_ONLY 硬过滤 > .rss_feeds.json 启用配置。"""
    try:
        if not OPML_PATH.exists():
            print(f"[server] OPML 不存在: {OPML_PATH}")
            return []
        root = ET.parse(str(OPML_PATH)).getroot()
        only_filter = os.environ.get("MYNEWS_RSS_ONLY", "").strip()
        prefs = _load_feed_prefs()
        feeds = []
        disabled = []
        for o in root.iter("outline"):
            url = (o.get("xmlUrl") or "").strip()
            name = (o.get("text") or o.get("title") or "").strip()
            if not url:
                continue
            if only_filter and url != only_filter:
                continue
            if not _feed_enabled(url, prefs):
                disabled.append(name or url)
                continue
            feeds.append({"name": name or url, "url": url})
        n_total = sum(1 for o in root.iter("outline") if (o.get("xmlUrl") or "").strip())
        print(f"[server] OPML 加载: {len(feeds)}/{n_total} 个 RSS 源（过滤: {'无' if not only_filter else only_filter}，禁用: {len(disabled)}）")
        return feeds
    except Exception as e:
        print(f"[server] OPML 解析失败: {e}")
        return []

FEEDS = _load_feeds()

def _fetch_rss_items(force: bool = False) -> list:
    """并发抓取所有源最新条目，合并去重并打乱，带缓存（RSS_INTERVAL*2 秒）。每次刷新重读 OPML（新增源即时生效）。"""
    global RSS_CACHE, FEEDS
    with RSS_LOCK:
        if not force and RSS_CACHE["items"] and (time.time() - RSS_CACHE["ts"] < RSS_INTERVAL * 2):
            return RSS_CACHE["items"]
    FEEDS = _load_feeds()  # 缓存过期时重新加载 OPML
    if not FEEDS:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_feed_items, f): f for f in FEEDS}
        for fut in futures:
            try:
                results.extend(fut.result())
            except Exception:
                pass
    # 按 URL 去重（保留首个），按发布时间倒序：最新条目优先
    seen, merged = set(), []
    for it in results:
        if it["url"] not in seen:
            seen.add(it["url"])
            merged.append(it)
    merged.sort(key=lambda x: x.get("ts", 0), reverse=True)
    with RSS_LOCK:
        RSS_CACHE = {"ts": time.time(), "items": merged}
    print(f"[server] RSS 聚合: {len(merged)} 条（{len(FEEDS)} 源），按发布时间倒序")
    return merged


def _rss_background_refresh():
    """后台守护线程：启动后立即拉取一次，之后每 RSS_INTERVAL 秒强制刷新 RSS 缓存。"""
    while True:
        try:
            _fetch_rss_items(force=True)
            print(f"[server] 后台 RSS 自动刷新完成（间隔 {RSS_INTERVAL}s）")
        except Exception as e:
            print(f"[server] 后台 RSS 自动刷新失败: {e}")
        time.sleep(RSS_INTERVAL)


# 后台自动处理开关：默认关闭；MYNEWS_AUTO_BG=1 强制开启 / =0 强制关闭（覆盖持久化状态）
# 持久化状态存 scripts/.auto_bg.json，前端切换或 curl API 修改后重启服务依然保持
AUTO_BG_STATE_FILE = SCRIPTS_DIR / ".auto_bg.json"
AUTO_BG_LOCK = threading.Lock()

def _load_auto_bg_state() -> bool:
    """启动时读取开关状态：环境变量显式设置优先，其次持久化文件，默认关。"""
    env = os.environ.get("MYNEWS_AUTO_BG")
    if env is not None:
        return env == "1"
    try:
        if AUTO_BG_STATE_FILE.exists():
            return json.loads(AUTO_BG_STATE_FILE.read_text(encoding="utf-8")).get("enabled", False)
    except Exception:
        pass
    return False

AUTO_BG_ENABLED = _load_auto_bg_state()

def _auto_bg_status() -> bool:
    """读取当前后台自动处理开关状态（运行时可变）。"""
    with AUTO_BG_LOCK:
        return AUTO_BG_ENABLED

def _set_auto_bg(enabled: bool):
    """运行时切换后台自动处理开关，并持久化到文件（重启后保持）。"""
    global AUTO_BG_ENABLED
    with AUTO_BG_LOCK:
        AUTO_BG_ENABLED = enabled
    try:
        AUTO_BG_STATE_FILE.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    except Exception as e:
        print(f"[auto] 持久化开关状态失败: {e}")
    print(f"[auto] 后台自动处理: {'开启' if enabled else '关闭'}")

_AP_MODULE = None

def _ap():
    """懒加载 auto_process 模块（供后台线程与 mark-processed 接口共用）。"""
    global _AP_MODULE
    if _AP_MODULE is None:
        sys.path.insert(0, str(SCRIPTS_DIR))
        # auto_process.py 顶层会检查 FLOMO_TOKEN，缺失时 sys.exit(1)——先注入避免杀死 server
        if FLOMO_TOKEN and not os.environ.get("FLOMO_TOKEN"):
            os.environ["FLOMO_TOKEN"] = FLOMO_TOKEN
        try:
            import auto_process
            _AP_MODULE = auto_process
        except SystemExit as e:
            print(f"[auto] auto_process.py 初始化退出（code={e}），后台自动处理不可用")
        except Exception as e:
            print(f"[auto] 无法加载 auto_process.py: {e}")
    return _AP_MODULE

def _ap_available() -> bool:
    return _ap() is not None

def _auto_background_process():
    """后台守护线程：每 RSS_INTERVAL 秒拉取 RSS，自动处理未处理过的条目（复用 auto_process.py）。
    已处理记录存 scripts/.auto_processed.json，成功/失败/抓取失败都会标记，避免重复。"""
    ap = _ap()
    if ap is None:
        return
    while True:
        if not _auto_bg_status():
            time.sleep(RSS_INTERVAL)
            continue
        try:
            items = ap.fetch_all_rss_items(limit_per_feed=3)
            # 本地缓存已处理集合：避免循环内每条都全量读文件+解析 JSON
            # mark_processed 第二参数会同步更新缓存，保持内存与文件一致
            processed = ap.load_processed()
            done = 0
            for it in items:
                # 处理前实时复查记录；未处理则先认领标记（防止与前端 ⚡ 竞态重复处理），再抓取/处理
                if it["url"] in processed:
                    continue
                ap.mark_processed(it["url"], processed)
                content, err = ap.fetch_article(it["url"])
                if err or not content:
                    print(f"  [auto] 抓取失败，标记跳过: {err or '空内容'} | {it['title'][:40]}")
                    continue
                ok, out = ap.process_article(content, it["url"])
                done += 1
                print(f"  [auto] {'成功' if ok else '失败'}: {it['title'][:40]}")
            print(f"[auto] 后台处理完成: {done} 条新条目")
        except Exception as e:
            print(f"[auto] 后台处理异常: {e}")
        time.sleep(RSS_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._serve_file(BASE_DIR / "webui" / "index.html", "text/html; charset=utf-8")
        elif self.path.startswith("/favicon"):
            self.send_response(204)
            self.end_headers()
        elif self.path.startswith("/api/auto-bg"):
            # 查询后台自动处理开关状态：/api/auto-bg
            self._json_response(True, {"enabled": _auto_bg_status()})
        elif self.path.startswith("/api/processed"):
            # 服务端已处理记录（与前端 localStorage 合并，避免重复处理）
            try:
                rec_file = SCRIPTS_DIR / ".auto_processed.json"
                urls = json.loads(rec_file.read_text(encoding="utf-8")) if rec_file.exists() else []
                self._json_response(True, {"urls": urls})
            except Exception:
                self._json_response(True, {"urls": []})
        elif self.path.startswith("/api/rss/items"):
            # RSS 聚合：全部源最新条目（含缓存）；?refresh=1 强制刷新缓存
            global RSS_CACHE
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("refresh") == ["1"]:
                RSS_CACHE = {"ts": 0.0, "items": []}
            self._json_response(True, _fetch_rss_items())
        elif self.path.startswith("/api/rss/stats"):
            # RSS 统计：源数量、条目总数、缓存时间、刷新间隔
            items = _fetch_rss_items()
            self._json_response(True, {
                "feeds": len(FEEDS),
                "items": len(items),
                "cache_ts": RSS_CACHE.get("ts", 0),
                "interval": RSS_INTERVAL,
                "auto_bg": _auto_bg_status(),
            })
        elif self.path.startswith("/api/rss/feeds"):
            # RSS 源列表及启用状态：GET /api/rss/feeds
            self._json_response(True, _feed_list())
        elif self.path.startswith("/api/fetch"):
            # URL 抓取预览：/api/fetch?url=xxx
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            url = (qs.get("url") or [""])[0].strip()
            if not url:
                self._json_response(False, "缺少 url 参数")
                return
            content = self._fetch_url(url, timeout=10)
            anti_patterns = ['环境异常', 'captcha', 'verify you are human', 'access denied']
            blocked = any(p in content[:200].lower() for p in anti_patterns)
            title = ""
            for line in content.split("\n")[:2]:
                if line.startswith("标题:"):
                    title = line[3:].strip()
                    break
            self._json_response(True, {
                "url": url, "title": title, "length": len(content),
                "blocked": blocked, "preview": content[:400]
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/process":
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ctype:
                # 手动解析 multipart form data（Python 3.13 已移除 cgi 模块）
                boundary = ctype.split("boundary=")[1].strip()
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                parts = raw.split(b"--" + boundary.encode())
                image_file = None
                for part in parts:
                    if b"Content-Disposition" not in part: continue
                    disp_line = part.split(b"\r\n", 2)[1].decode("utf-8", errors="replace")
                    name_match = re.search(r'name="([^"]+)"', disp_line)
                    if not name_match: continue
                    # 文件字段
                    if "filename=" in disp_line:
                        idx = part.find(b"\r\n\r\n") + 4
                        file_data = part[idx:].rstrip(b"\r\n--")
                        filename_match = re.search(r'filename="([^"]+)"', disp_line)
                        fname = filename_match.group(1) if filename_match else "upload.jpg"
                        image_file = type("FakeFile", (), {"file": type("FakeStream", (), {"read": lambda self, fd=file_data: fd})(), "filename": fname})()
                self._handle_process({"content": "", "url": "", "image": image_file})
            else:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
                self._handle_process(data)
        elif self.path.startswith("/api/auto-bg"):
            # 运行时切换后台自动处理：POST {"enabled": true|false}
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                _set_auto_bg(bool(body.get("enabled")))
                self._json_response(True, {"enabled": _auto_bg_status()})
            except Exception as e:
                self._json_response(False, f"切换失败: {e}")
        elif self.path.startswith("/api/rss/feeds/toggle"):
            # 切换单个源启用状态：POST {"url": "...", "enabled": true|false}
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                url = (body.get("url") or "").strip()
                if not url:
                    self._json_response(False, "缺少 url")
                    return
                prefs = _load_feed_prefs()
                prefs[url] = bool(body.get("enabled", True))
                _save_feed_prefs(prefs)
                global FEEDS, RSS_CACHE
                FEEDS = _load_feeds()
                RSS_CACHE = {"ts": 0.0, "items": []}
                self._json_response(True, {"url": url, "enabled": prefs[url], "feeds": _feed_list()})
            except Exception as e:
                self._json_response(False, f"切换失败: {e}")
        elif self.path.startswith("/api/mark-processed"):
            # 前端处理成功后通知服务端记录，复用 auto_process 的原子标记（带锁，不覆盖并发标记）
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                url = (body.get("url") or "").strip()
                if not url:
                    self._json_response(False, "缺少 url")
                    return
                if _ap_available():
                    _ap().mark_processed(url)
                else:
                    # 退化路径：直接写文件（尽力而为）
                    rec_file = SCRIPTS_DIR / ".auto_processed.json"
                    urls = json.loads(rec_file.read_text(encoding="utf-8")) if rec_file.exists() else []
                    if url not in urls:
                        urls.append(url)
                    rec_file.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
                self._json_response(True, "已记录")
            except Exception as e:
                self._json_response(False, f"记录失败: {e}")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_file(self, path, mime):
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def _handle_process(self, data):
        content = data.get("content", "").strip()
        url = data.get("url", "").strip()
        image_file = data.get("image", None)

        # URL 抓取：拉取网页正文
        if url and not content:
            content = self._fetch_url(url)
        # 检测反爬关键词（开头区域）
        anti_patterns = ['环境异常', 'captcha', 'verify you are human', 'access denied']
        if url and not image_file and any(p in content[:200].lower() for p in anti_patterns):
            print(f"[server] 检测到反爬拦截")
            self._json_response(False, f"⚠️ 该链接被反爬保护拦截。\n请手动打开链接完成验证后，复制正文粘贴到'正文'标签页。\n\n抓取内容预览：{content[:200]}")
            return

        # 图片上传处理
        if image_file and hasattr(image_file, "file") and image_file.filename:
            img_data = image_file.file.read()
            img_ext = Path(image_file.filename).suffix or ".jpg"
            import tempfile as _tf
            img_path = Path(_tf.gettempdir()) / f"mynews_upload_{os.urandom(4).hex()}{img_ext}"
            img_path.write_bytes(img_data)
            print(f"[server] 图片已保存: {img_path.name} ({len(img_data)} bytes)")

            # 使用 kimi 分析图片
            analysis = self._analyze_image(img_path)
            if analysis:
                content = analysis
                print(f"[server] 图片分析完成: {len(content)} chars")
            else:
                print(f"[server] 图片分析失败（kimi 不支持视觉或超时）")
                content = ""  # 后续交由用户手动粘贴
            # 清理临时文件
            try: img_path.unlink()
            except: pass
        else:
            image_file = None  # 确保没传图片时此字段为 None

        if url:
            content = f"[来自 URL: {url}]\n\n{content}" if content else f"[需要先抓取 URL: {url}]"

        if not content:
            self._json_response(False, "请提供正文内容、URL 或图片")
            return

        # 构建命令
        cmd = [
            sys.executable, str(SCRIPTS_DIR / "process_inbox.py"),
            "--content", content,
            "--auto"
        ]

        env = os.environ.copy()
        if FLOMO_TOKEN:
            env["FLOMO_TOKEN"] = FLOMO_TOKEN

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=600, env=env, cwd=str(SCRIPTS_DIR),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
            full_output = r.stdout or ""
            if r.stderr:
                full_output += "\n--- stderr ---\n" + r.stderr

            success = "上传成功" in full_output or "更新成功" in full_output or "处理完成" in full_output or "无增量 → 跳过" in full_output
            self._json_response(success, full_output)
        except subprocess.TimeoutExpired:
            self._json_response(False, "处理超时（>10分钟）")
        except Exception as e:
            self._json_response(False, f"执行错误: {e}")

    def _json_response(self, success, output):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"success": success, "output": output}, ensure_ascii=False).encode("utf-8"))

    def _fetch_url(self, url: str, timeout: int = 30) -> str:
        """从 URL 抓取正文（复用 rss_utils），返回提取的文本内容。失败返回空字符串。"""
        result, err = fetch_article_text(url, timeout=timeout)
        if result and not err:
            print(f"[server] URL 抓取成功: {url} ({len(result)} chars)")
            return result
        print(f"[server] URL 抓取失败: {err or '反爬拦截'} ({url})")
        return ""

    def _analyze_image(self, img_path: Path) -> str:
        """尝试用 kimi 分析图片内容，返回提取的文字。失败返回空字符串。"""
        from pathlib import Path as _P
        kimi_bin = str(_P.home() / ".kimi-code" / "bin" / "kimi")
        if not _P(kimi_bin).exists():
            kimi_bin = "kimi"
        prompt = f"请分析这张图片，提取其中的文字内容（保持原文的语言）。图片路径: {img_path}"
        try:
            r = subprocess.run(
                [kimi_bin, "-p", prompt, "--output-format", "text"],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )
            out = (r.stdout or r.stderr or "").strip()
            if out and "error" not in out[:50]:
                print(f"[server] 图片分析完成 ({len(out)} chars)")
                return out
        except: pass
        return ""

    def log_message(self, format, *args):
        # 参数数量不固定（如 send_error 只传 2 个），不能硬编码下标，否则 IndexError 导致请求线程崩溃
        try:
            sys.stderr.write(f"[webui] {format % args if args else format}\n")
        except Exception:
            sys.stderr.write(f"[webui] {format}\n")


if __name__ == "__main__":
    # 后台线程：每 10 分钟自动刷新 RSS 缓存
    threading.Thread(target=_rss_background_refresh, daemon=True).start()
    if AUTO_BG_ENABLED:
        threading.Thread(target=_auto_background_process, daemon=True).start()

    # IPv6 双栈监听（IPv4/IPv6 均可访问，兼容 localhost 解析为 ::1 的环境）
    class _DualStackHTTPServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6

        def server_bind(self):
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
            super().server_bind()

    try:
        server = _DualStackHTTPServer(("::", PORT), Handler)
    except OSError:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[mynews] Web UI 启动: http://localhost:{PORT}")
    print(f"   后台 RSS 自动刷新已启动（每 {RSS_INTERVAL} 秒）")
    print(f"   后台自动处理: {'已启动' if AUTO_BG_ENABLED else '已关闭（MYNEWS_AUTO_BG=0）'}")
    print(f"   按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()
