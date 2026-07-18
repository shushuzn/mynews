#!/usr/bin/env python3
"""
mynews inbox 处理器 (kimi 版本)
- 从 _inbox/ 读取条目（由 process_miniflux.py 生成）
- 调用 kimi 处理每条
- 成功后从 _inbox/ 移动到 _inbox_done/
- 失败后从 _inbox/ 移动到 _inbox_failed/
"""
import os
import sys
import json
import subprocess
import time
import argparse
import shutil
import re
import urllib.request
import urllib.parse
from pathlib import Path

from mynews_utils import (
    get_base_dir, get_opencode_bin, get_temp_dir, CrossPlatformLock,
    is_wechat_url, fetch_wechat_article
)

BASE_DIR = get_base_dir()
INBOX_DIR = BASE_DIR / "_inbox"
DONE_DIR = BASE_DIR / "_inbox_done"
FAILED_DIR = BASE_DIR / "_inbox_failed"
PROCESSED_FILE = BASE_DIR / "data" / "processed_urls.json"
PROCESSING_FILE = BASE_DIR / "data" / "processing_urls.json"
SEEN_FILE = INBOX_DIR / ".seen_ids.json"
STALE_TIMEOUT = 1800
LOCK_FILE = get_temp_dir() / "inbox_processor.lock"
KIMI_TIMEOUT = 300  # 5 分钟
PYTHON_BIN = sys.executable if sys.executable else ("python" if os.name == "nt" else "python3")
FLOMO_API_URL = "https://flomoapp.com/mcp"
FLOMO_TOKEN = "fmcp_tQUCgZl19bcH5slSicw2CotCJgw8V_1qdrHWs3w0Q8s"

# 确保所有目录存在
for d in [INBOX_DIR, DONE_DIR, FAILED_DIR,
          PROCESSED_FILE.parent,
          PROCESSING_FILE.parent]:
    d.mkdir(parents=True, exist_ok=True)


def load_processed():
    if PROCESSED_FILE.exists():
        with PROCESSED_FILE.open(encoding="utf-8") as f:
            return set(json.load(f).get("processed_urls", []))
    return set()


def save_processed(urls):
    existing = load_processed()
    updated = existing | set(urls)
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSED_FILE.open("w", encoding="utf-8") as f:
        json.dump({"processed_urls": sorted(list(updated))}, f, indent=2)


def load_processing():
    if PROCESSING_FILE.exists():
        with PROCESSING_FILE.open(encoding="utf-8") as f:
            return json.load(f).get("processing_urls", {})
    return {}


def save_processing_urls(processing_urls):
    PROCESSING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSING_FILE.open("w", encoding="utf-8") as f:
        json.dump({"processing_urls": processing_urls}, f, indent=2)


def remove_processing(url):
    processing = load_processing()
    processing.pop(url, None)
    save_processing_urls(processing)


def extract_source_info(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    source_url = ""
    source_type = "rss_entry"
    feed_title = ""
    entry_id = ""
    content_start = 0

    for i, line in enumerate(lines):
        if line.startswith("# SOURCE_URL") and i + 1 < len(lines):
            source_url = lines[i + 1].strip()
        elif line.startswith("# SOURCE_TYPE") and i + 1 < len(lines):
            source_type = lines[i + 1].strip()
        elif line.startswith("# FEED_TITLE") and i + 1 < len(lines):
            feed_title = lines[i + 1].strip()
        elif line.startswith("# ENTRY_ID") and i + 1 < len(lines):
            entry_id = lines[i + 1].strip()
        elif line.startswith("---"):
            content_start = i + 1
            break

    actual_content = "\n".join(lines[content_start:]).strip()
    return source_url, source_type, feed_title, entry_id, actual_content


def extract_wechat_if_needed(source_url, content, feed_title):
    """
    如果是微信公众号 URL 且内容为空或过短，尝试重新抓取
    返回: (content, feed_title)
    """
    if not is_wechat_url(source_url):
        return content, feed_title

    # 如果已有有效内容（超过500字符），直接返回
    if len(content) > 500:
        print(f"  [wechat] 已有足够内容 ({len(content)} 字符)，跳过重新抓取")
        return content, feed_title

    print(f"  [wechat] 检测到微信公众号 URL，尝试重新抓取...")
    print(f"    URL: {source_url[:60]}...")

    text, source, error = fetch_wechat_article(source_url, use_cache=True)

    if text:
        print(f"  [wechat] 抓取成功 ({source})，内容长度: {len(text)} 字符")
        # 从内容中提取标题
        if not feed_title:
            lines = text.strip().split("\n")
            if lines:
                feed_title = lines[0][:50]
        return text, feed_title or source
    else:
        print(f"  [wechat] 抓取失败: {error}")
        # 返回原始内容，让后续流程处理
        return content, feed_title


def move_to_failed(filepath, reason):
    basename = Path(filepath).name
    failed_path = FAILED_DIR / basename
    if Path(filepath).exists():
        shutil.move(filepath, failed_path)
    print(f"  [{basename}] Moved to failed: {reason}")


def move_to_done(filepath):
    basename = Path(filepath).name
    done_path = DONE_DIR / basename
    if Path(filepath).exists():
        shutil.move(filepath, done_path)


def call_kimi(prompt, timeout=KIMI_TIMEOUT):
    """调用 kimi 处理任务"""
    cmd = ["kimi", "-p", prompt]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode == 0:
            return stdout.strip(), stderr.strip()
        return "", f"kimi error: {stderr[:500]}"
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return "", "kimi timeout"
    except Exception as e:
        return "", f"kimi exception: {e}"


def search_flomo(keyword):
    """搜索 flomo"""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "memo_search",
            "arguments": {"keywords": keyword}
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        FLOMO_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {FLOMO_TOKEN}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
            # Parse SSE format
            for line in data.split("\n"):
                if line.startswith("data:"):
                    json_str = line[5:].strip()
                    if json_str:
                        result = json.loads(json_str)
                        if "result" in result:
                            return result["result"]
            return None
    except Exception as e:
        print(f"    [flomo search] error: {e}")
        return None


def upload_flomo(content):
    """上传到 flomo"""
    # 转义 content 中的下划线
    def escape_underscore_in_bold(match):
        return "**" + match.group(1).replace("_", "\\_") + "**"
    content_escaped = re.sub(
        r'^\*\*([^*]+)\*\*$', escape_underscore_in_bold, content, flags=re.MULTILINE
    )

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "memo_create",
            "arguments": {"content": content_escaped}
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        FLOMO_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {FLOMO_TOKEN}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
            # Parse SSE format
            for line in data.split("\n"):
                if line.startswith("data:"):
                    json_str = line[5:].strip()
                    if json_str:
                        result = json.loads(json_str)
                        if "result" in result:
                            memo = result["result"]
                            if "id" in memo:
                                return memo["id"]
                            # Check structuredContent
                            if "structuredContent" in memo and "id" in memo["structuredContent"]:
                                return memo["structuredContent"]["id"]
            return None
    except Exception as e:
        print(f"    [flomo upload] error: {e}")
        return None


def get_topic_keywords(source_type, content, feed_title):
    """提取主题关键词用于查重"""
    # 简单实现：从标题和内容提取关键词
    lines = content.strip().split("\n")
    title = ""
    for line in lines:
        if line.startswith("#"):
            title = line.strip("# ").strip()
            break
    
    if not title:
        # 尝试从第一行获取
        title = lines[0][:50] if lines else "unknown"
    
    return title, feed_title


def build_prompt(source_type, source_url, content, feed_title, filepath):
    """构建 kimi prompt"""
    topic_title, topic_source = get_topic_keywords(source_type, content, feed_title)
    
    return f"""**mynews 任务**：处理以下信息，生成 flomo 格式笔记并创建本地文件。

**信息内容**：
SOURCE_URL: {source_url}
来源: {feed_title or topic_source}
类型: {source_type}

{content[:2000]}

**步骤**：
1. 先用 MCP 工具 `memo_search` 搜索类似内容查重（搜索主题：{topic_title}）
2. 根据内容确定领域、二级领域、知识点（三段式）
3. 运行 `python3 scripts/check_dir.py <领域> <二级领域>` 确认目录存在
4. 运行 `python3 scripts/title_to_path.py "<标题>"` 获取完整路径
5. 创建 flomo 格式文件（见下方格式要求）
6. 在文件开头加上 `# SOURCE_URL {source_url}` 行
7. 运行 `python3 scripts/validate_flomo.py <文件路径>` 验证格式
8. 打印创建的文件路径

**⚠️ flomo 格式要求**：
```
#信号笔记 #领域 #二级领域

**领域_二级领域_知识点**

**来源**：{feed_title or topic_source}

正文内容...
```

**重要**：
- 第一行必须有 ≥3 个标签（含 #信号笔记）
- 标题必须是三段式 `领域_二级领域_知识点`
- 用 `**加粗**` 而不是 `#` 标题
- 禁止：链接、图片、表格、代码块
- 文件路径必须是 `answers/领域/二级领域/xxx.md`
- 验证通过后打印：`CREATED_FILE: <文件路径>`
"""


def get_next_file(source_type_filter="all"):
    """获取下一个待处理文件"""
    processed = load_processed()
    processing = load_processing()

    files = sorted([str(p) for p in INBOX_DIR.glob("*.md")])

    for f in files:
        source_url, source_type, _, _, _ = extract_source_info(f)
        if not source_url:
            continue
        if source_url in processed:
            continue
        if source_type_filter != "all" and source_type != source_type_filter:
            continue
        entry_time = processing.get(source_url)
        if entry_time is None:
            return f
        if isinstance(entry_time, (int, float)) and time.time() - entry_time > STALE_TIMEOUT:
            return f
    return None


def process_file(filepath, args):
    """处理单个文件"""
    basename = Path(filepath).name
    source_url, source_type, feed_title, entry_id, content = extract_source_info(filepath)

    # 微信公众号特殊处理：重新抓取正文
    content, feed_title = extract_wechat_if_needed(source_url, content, feed_title)

    if not source_url:
        print(f"  No SOURCE_URL found, moving to failed")
        move_to_failed(filepath, "no_source_url")
        return False

    if source_url in load_processed():
        print(f"  [{basename}] Already processed, removing")
        move_to_done(filepath)
        return True

    processing = load_processing()
    if source_url in processing:
        entry_time = processing[source_url]
        if isinstance(entry_time, (int, float)) and time.time() - entry_time > STALE_TIMEOUT:
            processing.pop(source_url, None)
            save_processing_urls(processing)
        else:
            print(f"  [{basename}] Being processed, skipping")
            return True

    processing[source_url] = time.time()
    save_processing_urls(processing)

    print(f"  Processing: {basename}")
    print(f"  Type: {source_type}, URL: {source_url[:60]}")

    # 构建 prompt 并调用 kimi
    prompt = build_prompt(source_type, source_url, content, feed_title, filepath)
    stdout, stderr = call_kimi(prompt, timeout=KIMI_TIMEOUT)

    if args.verbose:
        print(f"    stdout: {stdout[:500] if stdout else 'none'}")
        if stderr:
            print(f"    stderr: {stderr[:200]}")

    # 解析创建的文件路径
    created_file = None
    for line in stdout.split("\n"):
        if "CREATED_FILE:" in line:
            created_file = line.split("CREATED_FILE:")[-1].strip()
            break

    # 也检查 stderr 或重新查找最近创建的文件
    if not created_file:
        for line in stderr.split("\n"):
            if "CREATED_FILE:" in line:
                created_file = line.split("CREATED_FILE:")[-1].strip()
                break

    if not created_file:
        # 查找最近创建的 answers 文件
        answers_dir = BASE_DIR / "answers"
        if answers_dir.exists():
            candidates = [
                p for p in answers_dir.rglob("*.md")
                if p.is_file() and (time.time() - p.stat().st_mtime) < 300
            ]
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                created_file = candidates[0].relative_to(BASE_DIR).as_posix()
                print(f"    [fallback detect] 创建文件: {created_file}")

    if not created_file:
        print(f"    [error] 未检测到创建的文档")
        remove_processing(source_url)
        move_to_failed(filepath, "no_created_file_detected")
        return False

    # 验证文件格式
    full_path = BASE_DIR / created_file
    if not full_path.exists():
        print(f"    [error] 创建的文件不存在: {created_file}")
        remove_processing(source_url)
        move_to_failed(filepath, "file_not_found")
        return False

    # 运行 validate_flomo.py 验证
    validate_result = subprocess.run(
        [PYTHON_BIN, str(BASE_DIR / "scripts" / "validate_flomo.py"), created_file],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=60
    )

    if validate_result.returncode != 0:
        print(f"    [error] 格式验证失败")
        if args.verbose:
            print(f"    {validate_result.stdout[:300]}")
            print(f"    {validate_result.stderr[:300]}")
        remove_processing(source_url)
        move_to_failed(filepath, "validate_failed")
        return False

    print(f"    [ok] 文档验证通过: {created_file}")

    # 上传到 flomo
    with open(full_path, encoding="utf-8") as f:
        file_content = f.read()
    
    # 移除开头的 SOURCE_URL 行再上传
    content_lines = file_content.split("\n")
    if content_lines and "# SOURCE_URL" in content_lines[0]:
        content_lines = content_lines[1:]
    flomo_content = "\n".join(content_lines).strip()

    # 添加来源行
    source_line = f"**来源**：{feed_title or source_url}"
    if source_line not in flomo_content:
        # 找到第一个空行后插入
        lines = flomo_content.split("\n")
        for i, line in enumerate(lines):
            if not line.strip():
                lines.insert(i, source_line)
                break
        flomo_content = "\n".join(lines)

    flomo_id = upload_flomo(flomo_content)
    if flomo_id:
        print(f"    [flomo] 上传成功 id={flomo_id}")
        # 上传成功后删除本地文档
        # 先从staging区移除，避免误提交
        subprocess.run(
            ["git", "reset", "HEAD", "--", created_file],
            cwd=str(BASE_DIR),
            capture_output=True
        )
        if full_path.exists():
            full_path.unlink()
            print(f"    [cleanup] 已删除本地文档: {created_file}")
    else:
        print(f"    [flomo] 上传失败（文件已保存本地）")

    # 移动到 done
    remove_processing(source_url)
    save_processed([source_url])
    move_to_done(filepath)

    print(f"    [done] 处理完成")
    return True


ALLOWED_TOP_DIRS = {
    "医学", "安全", "技术", "政治", "教育科学", "法律",
    "游戏", "社会科学", "管理", "经济", "自然科学"
}


def slugify(text: str) -> str:
    """Convert text to a safe filename segment (keep Chinese, alphanumeric, hyphenate others).
    Truncates to 30 chars to keep filename manageable."""
    import re
    # Replace non-alphanumeric with hyphen (keep Chinese, letters, digits)
    text = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    return text[:30]


def ask_domain() -> str:
    """Interactive domain selection."""
    print("\n可选领域:")
    sorted_dirs = sorted(ALLOWED_TOP_DIRS)
    for i, d in enumerate(sorted_dirs, 1):
        print(f"  {i}. {d}")
    while True:
        choice = input("选择领域编号（或直接输入领域名）: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(sorted_dirs):
                return sorted_dirs[idx]
        # Try direct name match
        if choice in ALLOWED_TOP_DIRS:
            return choice
        print(f"  无效选择，请重试")


def ask_subdomain() -> str:
    """Interactive subdomain input."""
    while True:
        d = input("二级领域（如：AI芯片, 外交, 军事历史）: ").strip()
        if d and len(d) >= 2:
            return d
        print("  二级领域至少2个字符，请重试")


def ask_tags() -> list:
    """Interactive tag input."""
    print("信号类型标签（五选一）: #趋势信号 #知识基座 #信号笔记 #分析框架 #知识载体")
    while True:
        tag = input("输入信号类型标签（直接回车=信号笔记）: ").strip() or "#信号笔记"
        if tag.startswith('#') or tag.startswith('@'):
            break
        print("  标签必须以 # 或 @ 开头，请重试")
    extra = input("额外标签（如：#AI #开源，输入空格分隔，回车跳过）: ").strip()
    tags = [tag]
    if extra:
        tags.extend([t.strip() for t in extra.split() if t.strip()])
    return tags


def process_url(url: str, args):
    """处理单个 URL，直接完成 fetch → 构建 → 验证 → 上传全流程。"""
    from mynews_utils import is_wechat_url, fetch_wechat_article
    import urllib.request
    import urllib.error

    print(f"\n[URL 模式] {url[:60]}{'...' if len(url) > 60 else ''}")

    # 1. 抓取内容
    if is_wechat_url(url):
        print("  [wechat] 抓取中...")
        text, source, error = fetch_wechat_article(url, use_cache=True)
        if not text:
            print(f"  [error] 抓取失败: {error}")
            return False
        print(f"  [ok] 抓取成功 ({source})，{len(text)} 字符")
    else:
        print("  [http] 抓取中...")
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            # 简单提取 <title>
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else ""
            # 简单提取正文（取 <body> 内的文字）
            body_match = re.search(r'<body[^>]*>(.*)</body>', html, re.IGNORECASE | re.DOTALL)
            body = body_match.group(1) if body_match else html
            # 移除标签
            text = re.sub(r'<[^>]+>', ' ', body)
            text = re.sub(r'\s+', ' ', text).strip()
            print(f"  [ok] 抓取成功，{len(text)} 字符")
        except Exception as e:
            print(f"  [error] 抓取失败: {e}")
            return False

    # 2. 提取标题
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    # 取第一行非空行作为标题
    title = lines[0][:80] if lines else "未命名"
    if len(title) > 60:
        title = title[:57] + "..."
    print(f"  标题: {title}")

    # 3. 交互收集元信息
    interactive = not (hasattr(args, 'domain') and args.domain)
    print()
    if interactive:
        domain = ask_domain()
        subdomain = ask_subdomain()
        tags = ask_tags()
    else:
        domain = args.domain
        subdomain = args.subdomain if args.subdomain else ask_subdomain()
        tags = args.tags.split() if (hasattr(args, 'tags') and args.tags) else ["#信号笔记"]

    # 4. 交互收集正文内容
    if interactive:
        print(f"\n请输入正文内容（flomo 格式，用空行分隔段落，输入单独的 '.' 结束）:")
        print("  格式提示: **概念**：<mark>核心定义</mark>...  |  **子概念**： |  - 要点列表")
        print("  (输入 '.' 回车结束输入)\n")
        content_lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == '.':
                break
            content_lines.append(line)
        body_text = '\n'.join(content_lines).strip()
    else:
        if hasattr(args, 'content') and args.content:
            body_text = args.content
        else:
            print(f"\n请输入正文内容（flomo 格式，用空行分隔，输入 '.' 结束）:")
            print("  格式提示: **概念**：<mark>核心定义</mark>...  |  **子概念**： |  - 要点列表\n")
            content_lines = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip() == '.':
                    break
                content_lines.append(line)
            body_text = '\n'.join(content_lines).strip()

    if not body_text:
        print("  [error] 正文内容为空")
        return False

    # 5. 构建 flomo 内容
    knowledge = slugify(title)
    filename = f"{domain}_{subdomain}_{knowledge}.md"
    # 检查并修正段数
    name_part = filename[:-3]
    parts = name_part.split('_')
    # 如果超过3段，合并后几段
    while len(parts) > 3:
        parts[2] = parts[2] + '_' + parts[3]
        parts.pop(3)
    filename = '_'.join(parts) + ".md"

    full_path = BASE_DIR / "answers" / domain / subdomain / filename
    full_path.parent.mkdir(parents=True, exist_ok=True)

    tag_line = ' '.join(tags)
    bold_title = f"**{domain}_{subdomain}_{knowledge}**"

    flomo_content = f"""# {tag_line}

{bold_title}

**来源**：{title}

{body_text}
"""
    # 写文件
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(flomo_content)
    print(f"\n  [file] 已创建: answers/{domain}/{subdomain}/{filename}")

    # 6. 用 hook --staged 验证
    print("  [hook] 验证格式...")
    subprocess.run(["git", "add", "-f", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
    hook_result = subprocess.run(
        [PYTHON_BIN, str(BASE_DIR / "hooks" / "pre-commit"), "--staged"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True
    )
    if hook_result.returncode != 0:
        print(f"  [error] 格式验证失败:\n{hook_result.stdout}")
        subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
        # 保留文件让用户修正
        print(f"  [file] 文件保留在: {full_path}")
        return False

    print("  [ok] 格式验证通过")

    # 7. 上传到 flomo
    flomo_id = upload_flomo(flomo_content)
    if flomo_id:
        print(f"  [flomo] 上传成功 id={flomo_id}")
        # 清理
        subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
        full_path.unlink()
        print(f"  [cleanup] 已删除本地文件")
    else:
        print(f"  [flomo] 上传失败，文件保留在: {full_path}")

    print(f"\n✅ 处理完成!")
    return True


def main():
    parser = argparse.ArgumentParser(description="mynews inbox 处理器 (kimi 版本)")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="最多处理文件数 (默认 100, 单条用 --batch-size 1)")
    parser.add_argument("--source-type", choices=["rss_entry", "github_commit", "all"],
                        default="all", help="过滤源类型")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细输出")
    parser.add_argument("--url", type=str,
                        help="直接处理单个 URL（无需 inbox 文件，交互式输入正文）")
    parser.add_argument("--domain", type=str, help="领域（可选，配合 --url 使用，如 --domain 技术 --subdomain AI）")
    parser.add_argument("--subdomain", type=str, help="二级领域（可选）")
    parser.add_argument("--tags", type=str,
                        help="标签（可选，多个用空格分隔，如 --tags '#信号笔记 #AI'）")
    parser.add_argument("--content", type=str,
                        help="正文内容（可选，直接指定而非交互输入）")
    args = parser.parse_args()

    if args.url:
        process_url(args.url, args)
        return

    try:
        lock = CrossPlatformLock(LOCK_FILE)
        lock.acquire(blocking=False)
    except BlockingIOError:
        print("Another instance is running, exiting")
        return

    print(f"mynews inbox processor (kimi version)")
    print(f"  BASE_DIR: {BASE_DIR}")
    print(f"  INBOX_DIR: {INBOX_DIR}")
    print(f"  kimi timeout: {KIMI_TIMEOUT}s")
    print(f"  Batch size: {args.batch_size}, Source type: {args.source_type}")
    print()

    count = 0
    try:
        while count < args.batch_size:
            f = get_next_file(args.source_type)
            if not f:
                break

            if process_file(f, args):
                count += 1
            if count >= args.batch_size:
                print(f"\nProcessed {count} files, stopping for now")
                break

        if count == 0:
            print("No new files to process")
    finally:
        lock.release()


if __name__ == "__main__":
    main()
