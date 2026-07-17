#!/usr/bin/env python3
"""
检查目录是否存在并获取文件列表
用法: python3 check_dir.py "领域/二级领域"
"""

import sys
from pathlib import Path

from mynews_utils import setup_windows_utf8
setup_windows_utf8()

# 唯一允许的一级领域（按文档数排序）
ALLOWED_DOMAINS = {"医学", "安全", "技术", "政治", "教育科学", "法律", "游戏", "社会科学", "管理", "经济", "自然科学"}

def check_dir(domain: str, subdomain: str = None) -> dict:
    """
    检查目录是否存在，返回目录信息
    """
    base = Path("answers")

    if subdomain:
        dir_path = base / domain / subdomain
    else:
        dir_path = base / domain

    exists = dir_path.is_dir()
    files = []
    subdirs = []

    if exists:
        for item in dir_path.iterdir():
            if item.is_dir():
                subdirs.append(item.name)
            elif item.suffix == '.md':
                files.append(item.name)

    return {
        'path': dir_path.as_posix(),
        'exists': exists,
        'files': files,
        'subdirs': subdirs
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 check_dir.py \"领域\" [\"二级领域\"]")
        sys.exit(1)

    domain = sys.argv[1]
    subdomain = sys.argv[2] if len(sys.argv) > 2 else None

    if domain not in ALLOWED_DOMAINS:
        print(f"❌ 一级领域 '{domain}' 不在允许列表中（仅允许：{sorted(ALLOWED_DOMAINS)}），禁止新建")
        sys.exit(1)

    result = check_dir(domain, subdomain)

    print(f"目录: {result['path']}")
    print(f"存在: {result['exists']}")
    if result['exists']:
        if result['subdirs']:
            print(f"子目录: {', '.join(result['subdirs'])}")
        if result['files']:
            print(f"文件: {', '.join(result['files'])}")
    else:
        print("错误: 目录不存在")
        sys.exit(1)
