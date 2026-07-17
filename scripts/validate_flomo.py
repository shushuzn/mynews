#!/usr/bin/env python3
"""
mynews 答案文档格式审查工具
检查 flomo 格式合规性：
- 第一行 ≥3 个 #xxx 或 @xxx 标签
- 必含 #信号类型 之一
- 文件名三段式（领域_二级领域_知识点）
- 加粗标题必须以 文件名前两段_ 开头
- 无 # ## ### 标题
- 无 [text](url) 链接
- 无 ``` 代码块
- 无 --- 水平线
- 无 | 表格

用法: python3 scripts/validate_flomo.py <path/to/file.md>
退出码: 0 通过, 1 不通过
"""
import sys
import re
import os

SIGNAL_TYPES = {'#趋势信号', '#知识基座', '#信号笔记', '#分析框架', '#知识载体'}
FORBIDDEN_PATTERNS = [
    (re.compile(r'^#+\s+', re.MULTILINE), "Markdown 标题 (# ## ###)"),
    (re.compile(r'^>\s+', re.MULTILINE), "引用块 (>)"),
    (re.compile(r'```'), "代码块 (```)"),
    (re.compile(r'\[.+?\]\(.+?\)'), "链接 [text](url)"),
    (re.compile(r'!\[.*?\]\(.+?\)'), "图片 ![](url)"),
    (re.compile(r'^---+$', re.MULTILINE), "水平线 (---)"),
    (re.compile(r'^\|.+\|$', re.MULTILINE), "Markdown 表格"),
]


def validate_filepath(filepath):
    if not filepath.endswith('.md'):
        return [f"❌ 文件必须 .md 后缀"]
    errors = []
    rel = os.path.relpath(filepath)
    parts = rel.split('/')
    if len(parts) != 4 or parts[0] != 'answers':
        errors.append(f"❌ 路径必须 answers/领域/二级领域/文件名.md (4 层)，当前: {rel}")
        return errors
    if parts[1] not in {'医学', '安全', '技术', '政治', '教育科学', '法律', '游戏', '社会科学', '管理', '经济', '自然科学'}:
        errors.append(f"❌ 领域 '{parts[1]}' 不在白名单")
    filename = parts[3]
    name_no_ext = filename[:-3]
    name_parts = name_no_ext.split('_')
    if len(name_parts) < 3:
        errors.append(f"❌ 文件名必须三段式（领域_二级领域_知识点.md），当前 {len(name_parts)} 段: {filename}")
    return errors, parts[1], parts[2], filename


def validate_content(filepath, expected_domain, expected_subdomain, filename):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    lines = content.split('\n')
    errors = []

    if not lines or not lines[0].strip():
        errors.append("❌ 第一行不能为空，必须是标签行")
        return errors

    first_line = lines[0].strip()
    tags = re.findall(r'[#@][^\s#@]+', first_line)
    if len(tags) < 3:
        errors.append(f"❌ 标签数量不足3个，当前{len(tags)}个")
    if not any(t in tags for t in SIGNAL_TYPES):
        errors.append(f"❌ 标签必须含 #信号类型 之一: {sorted(SIGNAL_TYPES)}")

    for pattern, msg in FORBIDDEN_PATTERNS:
        if pattern.search(content):
            errors.append(f"❌ 包含禁止语法: {msg}")

    has_bold_title = False
    first_title_match = re.search(r'^\*\*([^*]+)\*\*$', content, re.MULTILINE)
    if not first_title_match:
        errors.append("❌ 缺少加粗标题（**xxx**），例如：**领域_二级领域_知识点**")
    else:
        has_bold_title = True
        title = first_title_match.group(1)
        name_no_ext = filename[:-3] if filename.endswith('.md') else filename
        file_prefix = '_'.join(name_no_ext.split('_')[:2])
        if not title.startswith(file_prefix + '_'):
            errors.append(f"❌ 加粗标题 '{title}' 必须以 '{file_prefix}_' 开头")

    # 检查来源行（必须是 "**来源**：xxx" 加粗格式）
    lines = content.split('\n')
    source_line_found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if i == 0:
            continue  # 标签行
        if first_title_match and stripped == f"**{title}**":
            continue  # 加粗标题
        # 期望是 "**来源**：xxx" 加粗格式
        if stripped.startswith("**来源**") or stripped.startswith("**来源:") or stripped.startswith("**来源："):
            source_line_found = True
            if "：" not in stripped and ":" not in stripped:
                errors.append(f"❌ 来源行 '{stripped[:50]}' 缺少冒号")
            break
        # 错例：用了 **出处**： 或不加粗的"来源："
        if stripped.startswith("**出处") or stripped.startswith("**来源") is False and stripped.startswith("来源"):
            if not stripped.startswith("**"):
                errors.append(f"❌ 来源行 '{stripped[:50]}' 不加粗，必须是 '**来源**：xxx'（加粗）")
            elif stripped.startswith("**出处"):
                errors.append(f"❌ 来源行 '{stripped[:50]}' 用了错误标签 '出处'，应该是 '**来源**'")
            source_line_found = True
            break
        # 第一个非空行不是来源
        break

    if not source_line_found:
        errors.append("❌ 缺少 '**来源**：xxx' 加粗行（必须在加粗标题之后）")

    return errors


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/validate_flomo.py <path/to/file.md>")
        sys.exit(1)
    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"❌ 文件不存在: {filepath}")
        sys.exit(1)

    result = validate_filepath(filepath)
    if isinstance(result, list):
        # 路径错误
        for e in result:
            print(e)
        sys.exit(1)
    else:
        errors_path, expected_domain, expected_subdomain, filename = result

    errors = validate_content(filepath, expected_domain, expected_subdomain, filename)
    if errors:
        for e in errors:
            print(e)
        sys.exit(1)

    print(f"✅ 格式合规: {filepath}")
    sys.exit(0)


if __name__ == "__main__":
    main()
