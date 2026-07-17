#!/usr/bin/env python3
"""
mynews inbox 处理器
- 从 _inbox/ 读取条目（由 process_miniflux.py 生成）
- 调用 doc-generator subagent 处理每条
- 成功后从 _inbox/ 移动到 _inbox_done/
- 失败后从 _inbox/ 移动到 _inbox_failed/
- 关键约束：answers 严禁 push 到远程；git reset 不能用 --hard（会删工作树）
"""
import os
import sys
import json
import subprocess
import glob
import fcntl
import time
import argparse
import shutil

BASE_DIR = "/root/mynews"
INBOX_DIR = os.path.join(BASE_DIR, "_inbox")
DONE_DIR = os.path.join(BASE_DIR, "_inbox_done")
FAILED_DIR = os.path.join(BASE_DIR, "_inbox_failed")
PROCESSED_FILE = os.path.join(BASE_DIR, "data", "processed_urls.json")
PROCESSING_FILE = os.path.join(BASE_DIR, "data", "processing_urls.json")
SEEN_FILE = os.path.join(INBOX_DIR, ".seen_ids.json")
STALE_TIMEOUT = 1800
EXEC_LOCK = os.path.join(BASE_DIR, "data", "opencode_run.lock")
MAX_CONCURRENT = 1
LOCK_FILE = "/tmp/inbox_processor.lock"
SUBAGENT_TIMEOUT = 900  # 15 分钟，单条文档处理全流程
OPENCODE_BIN = "/root/.opencode/bin/opencode"

# 确保所有目录存在
for d in [INBOX_DIR, DONE_DIR, FAILED_DIR,
          os.path.dirname(PROCESSED_FILE),
          os.path.dirname(PROCESSING_FILE)]:
    os.makedirs(d, exist_ok=True)


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set(data.get("seen_ids", []))
    return set()


def save_seen(urls):
    existing = load_seen()
    updated = existing | set(urls)
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(updated)), f)


def load_processed():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE) as f:
            return set(json.load(f).get("processed_urls", []))
    return set()


def save_processed(urls):
    existing = load_processed()
    updated = existing | set(urls)
    with open(PROCESSED_FILE, "w") as f:
        json.dump({"processed_urls": sorted(list(updated))}, f, indent=2)


def load_processing():
    if os.path.exists(PROCESSING_FILE):
        with open(PROCESSING_FILE) as f:
            return json.load(f).get("processing_urls", {})
    return {}


def save_processing_urls(processing_urls):
    with open(PROCESSING_FILE, "w") as f:
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
    content_start = 0

    for i, line in enumerate(lines):
        if line.startswith("# SOURCE_URL") and i + 1 < len(lines):
            source_url = lines[i + 1].strip()
        elif line.startswith("# SOURCE_TYPE") and i + 1 < len(lines):
            source_type = lines[i + 1].strip()
        elif line.startswith("---"):
            content_start = i + 1
            break

    actual_content = "\n".join(lines[content_start:]).strip()
    return source_url, source_type, actual_content


def build_prompt(source_type, source_url, content, filepath):
    """构建 subagent prompt"""
    common = f"""**mynews 项目 flomo 格式规范**：

- 本地文档直接写 flomo 格式，不是 4 章节 Markdown
- 第一行必须是标签行：≥3 个 `#xxx` 或 `@xxx` 标签
- 标题用 `**加粗**`，不是 `#` 标题
- 禁止：# 标题、引用块、代码块、链接、图片、水平线、表格
- 允许：**加粗**、`<mark>` 高亮、`<u>` 下划线、- 列表、1. 有序列表
- answers 严禁 push 到远程

**⚠️ 重要 - 文件名三段式（hook 强制检查）**：
文件名必须严格三段式 `<领域>_<二级领域>_<知识点>.md`，**不能省略前两段**！
- ✓ 正确示例：`自然科学_生态学_数字表型加速土壤生物多样性发现.md`
- ✗ 错误示例：`数字表型加速土壤生物多样性发现.md`（缺前缀，hook 会拒绝 commit）

**重要 - 工作目录**：所有 shell 命令必须在 /root/mynews 目录下执行。
所有命令前必须 `cd /root/mynews` 或者用绝对路径。

**⚠️ 严禁以下操作**（否则会破坏工作流）：
- ❌ `git reset --hard`（会删除工作树文件！务必避免）
- ❌ 任何 `git reset` / `git clean` / `git push`（git 操作由 process_inbox.py 接管）
- ❌ `mv`、`rm`、`cp` 操作 `_inbox/` 下的任何文件（由 process_inbox.py 接管）
- ❌ 任何会修改/删除 answers/ 下文件的命令

**subagent 唯一职责**：创建本地文档 + 调 `flomo_memo_create` 上传 + 打印 `CREATED_FILE: <path>` 退出。其他一切由 process_inbox.py 完成。"""

    if source_type == "github_commit":
        return f"""{common}

**任务**：处理以下 GitHub Commit 信息，生成结构化 flomo 笔记。

GitHub Commit 内容：
{content}

SOURCE_URL: {source_url}

**步骤**（每步用 shell 命令执行，所有命令都在 /root/mynews 目录）：
1. `cd /root/mynews`
2. 调用 MCP 工具 `flomo_memo_search` 查重（搜主题关键词）
3. 根据 commit 信息确定标题：`领域_二级领域_知识点` 三段式
4. `python3 /root/mynews/scripts/title_to_path.py "<标题>"` 获取完整路径
5. `python3 /root/mynews/scripts/check_dir.py <领域> <二级领域>` 确认目录
6. 创建本地文档 `answers/<领域>/<二级领域>/<知识点>.md`（flomo 格式：第一行标签 + `**加粗**` 标题）
7. `cd /root/mynews && git add <完整路径> && git commit -m "<message>"`（hook 自动验证 flomo 格式）
8. `cd /root/mynews && flomo_memo_create` 上传本地文档内容
9. `cd /root/mynews && git reset HEAD~1` 清除本地 commit（**严禁用 --hard，会删工作树文件！**）

不要提问，直接处理并报告处理结果。"""

    else:  # rss_entry
        return f"""{common}

**任务**：读取 inbox 文件 {filepath}，从中提取 SOURCE_URL，按 flomo 格式生成笔记并上传。

**步骤**（每步用 shell 命令执行，所有命令都在 /root/mynews 目录）：
1. `cd /root/mynews`
2. 读取 inbox 文件 {filepath} 提取 SOURCE_URL（应是 {source_url}）
3. 调用 MCP 工具 `flomo_memo_search` 查重（搜主题关键词 + 子领域）
4. Webfetch SOURCE_URL 获取完整文章内容
5. 从内容确定标题：`领域_二级领域_知识点` 三段式
6. `python3 /root/mynews/scripts/title_to_path.py "<标题>"` 获取完整路径
7. `python3 /root/mynews/scripts/check_dir.py <领域> <二级领域>` 确认目录
8. 创建本地文档 `answers/<领域>/<二级领域>/<知识点>.md`（flomo 格式：第一行标签 + `**加粗**` 标题）
9. `cd /root/mynews && git add <完整路径> && git commit -m "<message>"`（hook 自动验证 flomo 格式）
10. `cd /root/mynews && flomo_memo_create` 上传本地文档内容
11. `cd /root/mynews && git reset HEAD~1` 清除本地 commit（**严禁用 --hard，会删工作树文件！**）

**重要**：必须用完整路径（如 answers/科技/AI/xxx.md）执行 git add，不要用 git add -A。

不要提问，直接处理并报告处理结果。"""


def process_file(filepath, args):
    basename = os.path.basename(filepath)
    source_url, source_type, content = extract_source_info(filepath)

    if not source_url:
        print(f"  No SOURCE_URL found, moving to failed")
        move_to_failed(filepath, "no_source_url")
        return False

    if source_url in load_processed():
        print(f"  [{basename}] Already processed, removing")
        if os.path.exists(filepath):
            os.remove(filepath)
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

    prompt = build_prompt(source_type, source_url, content, filepath)
    cmd = [OPENCODE_BIN, "run", "--agent", "doc-generator", prompt]

    print(f"  Calling subagent (timeout {SUBAGENT_TIMEOUT}s)...")
    proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    cleanup_success = False
    error_msg = None
    stdout = ""
    try:
        stdout, stderr = proc.communicate(timeout=SUBAGENT_TIMEOUT)
        if proc.returncode == 0:
            print(f"    Subagent done")
            if args.verbose and stdout:
                print(f"    stdout: {stdout[:500]}")
            cleanup_success = True
        else:
            error_msg = (stderr or "unknown")[:300]
            print(f"    Subagent error: {error_msg}")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        error_msg = f"timeout after {SUBAGENT_TIMEOUT}s"
        print(f"    {error_msg}")

    # 从 stdout 解析 subagent 创建的文件路径
    created_file = None
    if cleanup_success:
        import re
        # 模式 1: 显式 CREATED_FILE: 标记
        m = re.search(r'CREATED_FILE:\s*(\S+\.md)', stdout)
        if m:
            created_file = m.group(1).strip()
            print(f"    [detect] 创建文件: {created_file}")
        # 模式 2: stdout 文本中匹配 answers/领域/二级领域/xxx.md 模式
        if not created_file:
            candidates = re.findall(r'answers/[\u4e00-\u9fff]+/[\u4e00-\u9fff]+/[^\s\)]+\.md', stdout)
            if candidates:
                created_file = candidates[0]
                print(f"    [text detect] 创建文件: {created_file}")
        # 模式 3: find 最新 answers/.../*.md
        if not created_file:
            result = subprocess.run(
                ["find", os.path.join(BASE_DIR, "answers"),
                 "-name", "*.md", "-mmin", "-10", "-type", "f"],
                capture_output=True, text=True
            )
            candidates = [c for c in result.stdout.strip().split("\n") if c]
            if candidates:
                # 取最近修改的
                candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                created_file = os.path.relpath(candidates[0], BASE_DIR)
                print(f"    [fallback detect] 创建文件: {created_file}")

    if not created_file:
        error_msg = error_msg or "no_created_file_detected"
        cleanup_success = False
        print(f"    [error] 未检测到创建的文档")

    if cleanup_success and created_file:
        full_path = os.path.join(BASE_DIR, created_file)
        if not os.path.exists(full_path):
            cleanup_success = False
            error_msg = f"file_not_found: {created_file}"
            print(f"    [error] 文件不存在: {full_path}")
        else:
            # 修正三段式文件名（subagent 可能漏前缀）
            parts = created_file.split('/')
            if len(parts) == 4 and parts[0] == 'answers':
                filename = parts[3]
                name_no_ext = filename[:-3] if filename.endswith('.md') else filename
                sub_parts = name_no_ext.split('_')
                if len(sub_parts) < 3 or sub_parts[0] != parts[1] or sub_parts[1] != parts[2]:
                    # 文件名漏了或前缀错了
                    old_path = full_path
                    if len(sub_parts) < 3:
                        new_filename = f"{parts[1]}_{parts[2]}_{filename}"
                    else:
                        new_filename = f"{parts[1]}_{parts[2]}_{'_'.join(sub_parts[2:])}.md"
                    new_rel = f"answers/{parts[1]}/{parts[2]}/{new_filename}"
                    new_full = os.path.join(BASE_DIR, new_rel)
                    print(f"    [fix] 文件名修正: {filename} → {new_filename}")
                    os.rename(old_path, new_full)
                    created_file = new_rel

            # process_inbox 接管 git 操作
            add_result = subprocess.run(
                ["git", "add", "-f", created_file],
                cwd=BASE_DIR, capture_output=True, text=True
            )
            if add_result.returncode != 0:
                cleanup_success = False
                error_msg = f"git_add_failed: {add_result.stderr[:200]}"
                print(f"    [error] git add 失败: {error_msg}")
            else:
                # git commit
                title = os.path.basename(created_file).replace(".md", "")
                commit_msg = f"创建 {title}"
                commit_result = subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=BASE_DIR, capture_output=True, text=True
                )
                if commit_result.returncode != 0 or "验证失败" in commit_result.stderr:
                    print(f"    [warn] commit 失败:")
                    print(f"    {commit_result.stderr[:300]}")
                    cleanup_success = False
                    error_msg = f"commit_failed: {commit_result.stderr[:200]}"
                else:
                    # git reset HEAD~1 (不 hard)
                    reset_result = subprocess.run(
                        ["git", "reset", "HEAD~1"],
                        cwd=BASE_DIR, capture_output=True, text=True
                    )
                    if reset_result.returncode == 0:
                        print(f"    [git] add -f + commit + reset HEAD~1 OK")
                    else:
                        print(f"    [warn] reset 失败: {reset_result.stderr[:200]}")

    remove_processing(source_url)
    if cleanup_success:
        save_processed([source_url])
        try:
            move_to_done(filepath)
        except FileNotFoundError:
            print(f"    [note] inbox 文件已被 subagent 移走，跳过 move")
    else:
        try:
            move_to_failed(filepath, error_msg or "subagent_failed")
        except FileNotFoundError:
            print(f"    [note] inbox 文件已被 subagent 移走，跳过 move")

    return cleanup_success


def move_to_done(filepath):
    dest = os.path.join(DONE_DIR, os.path.basename(filepath))
    if os.path.exists(dest):
        os.remove(dest)
    shutil.move(filepath, dest)
    print(f"    [moved] → _inbox_done/")


def move_to_failed(filepath, reason):
    dest = os.path.join(FAILED_DIR, os.path.basename(filepath))
    if os.path.exists(dest):
        os.remove(dest)
    shutil.move(filepath, dest)
    # 写入失败原因
    reason_file = dest + ".reason"
    with open(reason_file, "w") as f:
        f.write(f"{reason}\n")
    print(f"    [moved] → _inbox_failed/ ({reason})")


def get_next_file(source_type_filter="all"):
    processed = load_processed()
    processing = load_processing()

    files = sorted(glob.glob(os.path.join(INBOX_DIR, "*.md")))

    for f in files:
        source_url, source_type, _ = extract_source_info(f)
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


def main():
    parser = argparse.ArgumentParser(description="mynews inbox 处理器")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="最多处理文件数 (默认 100, 单条用 --batch-size 1)")
    parser.add_argument("--source-type", choices=["rss_entry", "github_commit", "all"],
                        default="all", help="过滤源类型")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示 subagent 输出")
    args = parser.parse_args()

    lock_fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another instance is running, exiting")
        lock_fd.close()
        return

    print(f"mynews inbox processor")
    print(f"  BASE_DIR: {BASE_DIR}")
    print(f"  INBOX_DIR: {INBOX_DIR}")
    print(f"  Subagent timeout: {SUBAGENT_TIMEOUT}s")
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
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)


if __name__ == "__main__":
    main()
