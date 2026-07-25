#!/usr/bin/env python3
"""
mynews Web UI 服务器
启动: python3 server.py [端口]
前端: http://localhost:8080
"""
import os, sys, json, subprocess, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._serve_file(BASE_DIR / "webui" / "index.html", "text/html; charset=utf-8")
        elif self.path.startswith("/favicon"):
            self.send_response(204)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/process":
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
        force_new = data.get("forceNew", False)

        if url:
            content = f"[来自 URL: {url}]\n\n{content}" if content else f"[需要先抓取 URL: {url}]"

        if not content:
            self._json_response(False, "请提供正文内容或 URL")
            return

        # 构建命令
        cmd = [
            sys.executable, str(SCRIPTS_DIR / "process_inbox.py"),
            "--content", content,
            "--auto"
        ]
        if force_new:
            cmd.append("--force-new")

        env = os.environ.copy()
        env["FLOMO_TOKEN"] = env.get("FLOMO_TOKEN", "")

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, cwd=str(SCRIPTS_DIR))
            full_output = r.stdout
            if r.stderr:
                full_output += "\n--- stderr ---\n" + r.stderr

            success = "上传成功" in r.stdout or "✅ 处理完成" in r.stdout
            self._json_response(success, full_output)
        except subprocess.TimeoutExpired:
            self._json_response(False, "处理超时（>5分钟）")
        except Exception as e:
            self._json_response(False, f"执行错误: {e}")

    def _json_response(self, success, output):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"success": success, "output": output}, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        sys.stderr.write(f"[webui] {args[0]} {args[1]} {args[2]}\n")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🌐 mynews Web UI 启动: http://localhost:{PORT}")
    print(f"   按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()
