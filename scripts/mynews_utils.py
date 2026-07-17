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
