#!/usr/bin/env python3
"""
生成标准化的 commit message
用法: python3 gen_commit.py "Add" "科技_AI_大模型发布"
"""

import sys

PREFIX_MAP = {
    "add": "Add",
    "update": "Update",
    "fix": "Fix",
    "docs": "docs",
}

DOMAIN_PREFIX = {
    "医学": "医学",
    "安全": "安全",
    "技术": "技术",
    "政治": "政治",
    "教育科学": "教育科学",
    "法律": "法律",
    "游戏": "游戏",
    "社会科学": "社会科学",
    "管理": "管理",
    "经济": "经济",
    "自然科学": "自然科学",
}

def gen_commit_msg(action: str, title: str) -> str:
    """
    生成标准 commit message
    """
    prefix = PREFIX_MAP.get(action.lower(), action)

    # 从标题提取关键信息
    parts = title.split('_')
    if len(parts) >= 3:
        domain = parts[0]
        # subdomain = parts[1]
        topic = parts[2] if len(parts) > 2 else parts[-1]
    else:
        topic = title

    # 生成消息
    if action.lower() == "add":
        return f"Add: {title.replace('_', '/')}"
    elif action.lower() == "update":
        return f"Update: {title.replace('_', '/')}"
    elif action.lower() == "fix":
        return f"Fix: {title.replace('_', '/')}"
    else:
        return f"{prefix}: {title.replace('_', '/')}"

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3 gen_commit.py <action> <title>")
        print("action: add, update, fix, docs")
        sys.exit(1)

    action = sys.argv[1]
    title = sys.argv[2]

    print(gen_commit_msg(action, title))
