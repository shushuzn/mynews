#!/usr/bin/env python3
"""
标题验证工具（flomo 版）
验证标题格式是否正确

用法: python3 scripts/check_title.py "领域_二级领域_知识点"
"""

import sys
import re

ALLOWED_DOMAINS = {"医学", "安全", "技术", "政治", "教育科学", "法律", "游戏", "社会科学", "管理", "经济", "自然科学"}


def validate_title(title: str) -> tuple:
    """验证标题格式，返回(bool, error_msg)"""
    parts = title.split('_')
    if len(parts) < 3:
        return False, f"标题必须包含至少3个下划线分隔的部分 (领域_二级领域_知识点)，收到: {title}"
    for part in parts:
        if '/' in part or '\\' in part:
            return False, f"标题各部分不能包含斜杠，收到: {title}"
    if len(parts[0]) < 1 or len(parts[1]) < 1:
        return False, f"领域和二级领域不能为空，收到: {title}"
    return True, ""


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/check_title.py \"领域_二级领域_知识点\"")
        sys.exit(1)

    title = sys.argv[1]

    valid, err_msg = validate_title(title)
    if not valid:
        print(f"❌ {err_msg}")
        sys.exit(1)

    print(f"✅ 标题格式正确: {title}")
    parts = title.split('_')
    domain, subdomain = parts[0], parts[1]
    print(f"领域: {domain}, 二级领域: {subdomain}, 知识点: {parts[2] if len(parts) > 2 else ''}")

    if domain not in ALLOWED_DOMAINS:
        print(f"⚠️  一级领域 '{domain}' 不在标准列表中（{sorted(ALLOWED_DOMAINS)}）")


if __name__ == "__main__":
    main()
