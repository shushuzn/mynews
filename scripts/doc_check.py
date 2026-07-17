#!/usr/bin/env python3
"""
文档预检查工具（合并版）
一次性完成：重复检查 → 路径生成 → 目录验证

用法: python3 scripts/doc_check.py "领域_二级领域_知识点"
"""

import sys
import re
import os
from pathlib import Path

BASE_DIR = "answers"

# 唯一允许的一级领域（按文档数排序）
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


def extract_keywords(title: str) -> list:
    parts = title.split('_')
    if len(parts) >= 3:
        keywords = parts[2:]
    else:
        keywords = parts
    return [k for k in keywords if len(k) >= 2]


def check_duplicate(title: str) -> list:
    keywords = extract_keywords(title)
    found = []
    answers_path = Path(BASE_DIR)

    parts = title.split('_')
    if len(parts) >= 2:
        search_path = answers_path / parts[0] / parts[1]
        if search_path.exists():
            answers_path = search_path

    if not answers_path.exists():
        return found

    for md_file in answers_path.rglob("*.md"):
        content = md_file.read_text(encoding='utf-8')
        title_match = re.search(r'^\*\*([^*]+)\*\*$', content, re.MULTILINE)
        if not title_match:
            continue
        file_title = title_match.group(1).strip()
        for kw in keywords:
            if kw in file_title:
                found.append({
                    'path': str(md_file),
                    'title': file_title,
                    'keyword': kw
                })
                break
    return found


def title_to_path(title: str) -> tuple:
    parts = title.split('_')
    domain = parts[0]
    subdomain = parts[1]
    filename_parts = parts[2:]
    dir_path = f"{BASE_DIR}/{domain}/{subdomain}"
    filename = '_'.join(filename_parts) + ".md"
    full_path = f"{dir_path}/{filename}"
    return full_path, dir_path, filename


def check_dir(domain: str, subdomain: str) -> dict:
    dir_path = f"{BASE_DIR}/{domain}/{subdomain}"
    exists = os.path.isdir(dir_path)
    files = []
    subdirs = []

    if exists:
        for item in os.listdir(dir_path):
            item_path = os.path.join(dir_path, item)
            if os.path.isdir(item_path):
                subdirs.append(item)
            elif item.endswith('.md'):
                files.append(item)

    return {
        'path': dir_path,
        'exists': exists,
        'files': files,
        'subdirs': subdirs
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/doc_check.py \"领域_二级领域_知识点\"")
        sys.exit(1)

    title = sys.argv[1]

    valid, err_msg = validate_title(title)
    if not valid:
        print(f"❌ {err_msg}")
        sys.exit(1)

    print(f"标题: {title}")
    parts = title.split('_')
    domain, subdomain = parts[0], parts[1]
    print(f"领域: {domain}, 二级领域: {subdomain}")
    print()

    if domain not in ALLOWED_DOMAINS:
        print(f"❌ 一级领域 '{domain}' 不在允许列表中（仅允许：{sorted(ALLOWED_DOMAINS)}），禁止新建")
        sys.exit(1)

    if not os.path.isdir(os.path.join(BASE_DIR, domain)):
        print(f"❌ 一级领域 '{domain}' 不存在，禁止新建")
        sys.exit(1)

    dir_result = check_dir(domain, subdomain)

    if not dir_result['exists']:
        os.makedirs(dir_result['path'], exist_ok=True)
        print("✅ 目录已创建，重复检查跳过")

    duplicate_result = check_duplicate(title)
    if duplicate_result:
        print(f"⚠️  发现 {len(duplicate_result)} 个可能重复的文档:")
        for item in duplicate_result:
            print(f"  - {item['path']}")
            print(f"    标题: {item['title']}")
            print(f"    匹配: {item['keyword']}")
        print("根据'同一概念判断'规则决定: 更新/合并/新建")
    else:
        print("✅ 未发现重复文档，可以新建")

    print()

    full_path, dir_path, filename = title_to_path(title)
    print(f"目录: {dir_path}")
    print(f"文件名: {filename}")
    print(f"完整路径: {full_path}")

    print()

    dir_result = check_dir(domain, subdomain)

    print(f"目录: {dir_result['path']}")
    print(f"存在: {dir_result['exists']}")
    if dir_result['exists']:
        if dir_result['subdirs']:
            print(f"子目录: {', '.join(dir_result['subdirs'])}")
        if dir_result['files']:
            print(f"文件: {', '.join(dir_result['files'])}")


if __name__ == "__main__":
    main()
