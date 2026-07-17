#!/usr/bin/env python3
"""
标题转路径工具
用法: python3 title_to_path.py "领域_二级领域_知识点"
"""

import sys
import re
import os

def title_to_path(title: str) -> str:
    """
    将三段式标题转换为文件路径
    标题格式: 领域_二级领域_知识点
    路径格式: answers/领域/二级领域/知识点.md
    """
    # 按下划线分割
    parts = title.split('_')

    if len(parts) < 3:
        print(f"错误: 标题必须包含至少3个下划线分隔的部分 (领域_二级领域_知识点)")
        print(f"收到: {title}")
        return None

    # 前两段是目录，第三段及之后是文件名
    domain = parts[0]           # 领域
    subdomain = parts[1]        # 二级领域
    filename_parts = parts[2:]  # 知识点（可能多段）

    # 构建路径
    dir_path = f"answers/{domain}/{subdomain}"
    filename = '_'.join(filename_parts) + ".md"
    full_path = f"{dir_path}/{filename}"

    return full_path, dir_path, filename

def validate_title(title: str) -> bool:
    """验证标题格式"""
    parts = title.split('_')
    if len(parts) < 3:
        return False
    # 检查是否包含禁止字符
    for part in parts:
        if '/' in part or '\\' in part:
            return False
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 title_to_path.py \"领域_二级领域_知识点\"")
        sys.exit(1)

    title = sys.argv[1]

    if not validate_title(title):
        print(f"错误: 标题格式不正确")
        sys.exit(1)

    result = title_to_path(title)
    if result:
        full_path, dir_path, filename = result
        print(f"标题: {title}")
        print(f"目录: {dir_path}")
        print(f"文件名: {filename}")
        print(f"完整路径: {full_path}")
