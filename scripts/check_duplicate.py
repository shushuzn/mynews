#!/usr/bin/env python3
"""检查是否已存在相关文档"""

import sys
import os
import re
from pathlib import Path

def extract_keywords(title: str) -> list:
    """从标题提取关键词（去除领域、二级领域）"""
    parts = title.split('_')
    if len(parts) >= 3:
        keywords = parts[2:]
    else:
        keywords = parts
    return [k for k in keywords if len(k) >= 2]

def search_in_answers(keywords: list, answers_dir: str = "answers") -> list:
    """在answers目录搜索包含关键词的文件"""
    found = []
    answers_path = Path(answers_dir)
    if not answers_path.exists():
        return found

    title = sys.argv[1] if len(sys.argv) > 1 else ""
    parts = title.split('_')
    if len(parts) >= 2:
        search_path = answers_path / parts[0] / parts[1]
        if search_path.exists():
            answers_path = search_path

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

def main():
    if len(sys.argv) < 2:
        print("用法: python3 check_duplicate.py \"标题\"")
        sys.exit(1)

    title = sys.argv[1]
    keywords = extract_keywords(title)

    print(f"标题: {title}")
    print(f"关键词: {keywords}")
    print()

    found = search_in_answers(keywords)

    if found:
        print(f"⚠️  发现 {len(found)} 个可能重复的文档:")
        for item in found:
            print(f"  - {item['path']}")
            print(f"    标题: {item['title']}")
            print(f"    匹配: {item['keyword']}")
        print()
        print("根据'同一概念判断'规则决定: 更新/合并/新建")
    else:
        print("✅ 未发现重复文档，可以新建")

    return found

if __name__ == "__main__":
    main()
