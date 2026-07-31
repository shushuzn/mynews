#!/usr/bin/env python3
"""
mynews Web UI 服务器
启动: python3 server.py [端口]
前端: http://localhost:8080
"""
import os, sys, json, subprocess, urllib.parse, threading, tempfile, re
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

# 优先环境变量，其次 .env 文件
FLOMO_TOKEN = os.environ.get("FLOMO_TOKEN")
if not FLOMO_TOKEN:
    env_file = BASE_DIR / ".flomo_env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("FLOMO_TOKEN="):
                FLOMO_TOKEN = line.split("=", 1)[1].strip().strip("\"'")
                break


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._serve_file(BASE_DIR / "webui" / "index.html", "text/html; charset=utf-8")
        elif self.path.startswith("/favicon"):
            self.send_response(204)
            self.end_headers()
        elif self.path.startswith("/api/hn/newest"):
            # HN 最新文章列表：/api/hn/newest
            self._json_response(True, self._fetch_hn_newest())
        elif self.path.startswith("/api/fetch"):
            # URL 抓取预览：/api/fetch?url=xxx
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            url = (qs.get("url") or [""])[0].strip()
            if not url:
                self._json_response(False, "缺少 url 参数")
                return
            content = self._fetch_url(url)
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
                               timeout=600, env=env, cwd=str(SCRIPTS_DIR))
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

    def _fetch_url(self, url: str) -> str:
        """从 URL 抓取正文，返回提取的文本内容。失败返回空字符串。"""
        import urllib.request as _ur, re as _re
        try:
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            with _ur.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            # 提取 title
            title_m = _re.search(r'<title[^>]*>([^<]+)</title>', html, _re.IGNORECASE)
            title = title_m.group(1).strip() if title_m else ""
            # 提取正文：先去掉 script/style，再取 body 或全文文本
            clean = _re.sub(r'<script[^>]*>.*?</script>', '', html, flags=_re.DOTALL)
            clean = _re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=_re.DOTALL)
            clean = _re.sub(r'<[^>]+>', '\n', clean)
            clean = _re.sub(r'\n{3,}', '\n\n', clean).strip()
            # 截断过长的内容（取前 8000 字符）
            body = clean[:8000]
            result = (f"标题: {title}\n\n{body}" if title else body).strip()
            print(f"[server] URL 抓取成功: {url} ({len(result)} chars)")
            return result
        except Exception as e:
            print(f"[server] URL 抓取失败: {e}")
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
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60
            )
            out = (r.stdout or r.stderr or "").strip()
            if out and "error" not in out[:50]:
                print(f"[server] 图片分析完成 ({len(out)} chars)")
                return out
        except: pass
        return ""

    def _fetch_hn_newest(self) -> list:
        """通过 Algolia HN API 获取最新文章，返回 [{title, url}]。"""
        import urllib.request as _ur, json as _json
        try:
            req = _ur.Request(
                "https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=30",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with _ur.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read().decode("utf-8", errors="replace"))
            items = []
            for hit in data.get("hits", []):
                url = (hit.get("url") or "").strip()
                title = (hit.get("title") or "").strip()
                if url and title:
                    items.append({"title": title, "url": url})
            print(f"[server] HN newest 抓取成功: {len(items)} 条")
            return items[:30]
        except Exception as e:
            print(f"[server] HN 抓取失败: {e}")
            return []

    def log_message(self, format, *args):
        sys.stderr.write(f"[webui] {args[0]} {args[1]} {args[2]}\n")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[mynews] Web UI 启动: http://localhost:{PORT}")
    print(f"   按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()
