#!/usr/bin/env python3
"""
上传验证通过的文档到 flomo
用法: python3 scripts/upload_flomo.py <file_path>
"""

import sys
import subprocess
import re


def read_file_from_git(file_path):
    result = subprocess.run(
        ["git", "show", f":{file_path}"],
        capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode == 0:
        return result.stdout
    return None


def extract_title(content):
    match = re.search(r'^# (.+)', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def extract_tags(content):
    match = re.search(r'^## 分类标签\s*\n(.+)$', content, re.MULTILINE)
    if match:
        tags_line = match.group(1).strip()
        tags = re.findall(r'[#@][^\s#@]+', tags_line)
        return ' '.join(tags)
    return ""


def convert_to_flomo_format(content, title, tags):
    lines = content.split('\n')
    flomo_lines = [tags, ""]
    
    flomo_lines.append(f"**{title}**")
    flomo_lines.append("")
    
    current_section = None
    in_core_concepts = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        if stripped.startswith('## '):
            section_name = stripped[3:].strip()
            if section_name == "来源":
                current_section = "来源"
            elif section_name == "概述":
                current_section = "概述"
            elif section_name == "核心概念":
                current_section = "核心概念"
                in_core_concepts = True
            elif section_name == "分类标签":
                current_section = "分类标签"
            else:
                current_section = None
            continue
        
        if current_section == "来源" and stripped and not stripped.startswith('#'):
            flomo_lines.append(f"来源：{stripped}")
        elif current_section == "概述" and stripped:
            flomo_lines.append(f"{stripped}")
        elif current_section == "核心概念":
            if stripped.startswith('### '):
                concept_name = stripped[4:].strip()
                flomo_lines.append(f"**{concept_name}**：")
            elif stripped and not stripped.startswith('## ') and not stripped.startswith('#'):
                flomo_lines.append(stripped)
        elif current_section == "分类标签":
            break
    
    return '\n'.join(flomo_lines)


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/upload_flomo.py <file_path>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    content = read_file_from_git(file_path)
    
    if not content:
        print(f"无法读取文件: {file_path}")
        sys.exit(1)
    
    title = extract_title(content)
    if not title:
        print("无法提取标题")
        sys.exit(1)
    
    tags = extract_tags(content)
    if not tags:
        print("无法提取标签")
        sys.exit(1)
    
    flomo_content = convert_to_flomo_format(content, title, tags)
    
    print("=== Flomo 上传内容 ===")
    print(flomo_content)
    print()
    print("请使用 flomo_memo_create 上传")


if __name__ == "__main__":
    main()
