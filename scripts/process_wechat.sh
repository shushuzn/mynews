#!/bin/bash
# process_wechat.sh - 唯一入口，强制先笔记再处理
# 用法: bash process_wechat.sh <url>
# 必须先在 notes.md 中记录该 URL 的抓取内容，否则拒绝执行

URL="$1"
NOTES="/root/.local/share/mimocode/memory/sessions/ses_08c4f6069ffeNrx5mrwd6kJF0B/notes.md"

if [ -z "$URL" ]; then
    echo "用法: process_wechat.sh <url>"
    exit 1
fi

# 检查 notes.md 中是否有该 URL 的抓取记录
if ! grep -q "$URL" "$NOTES" 2>/dev/null; then
    echo "错误: 必须先抓取文章并存入 notes.md 才能处理此 URL"
    echo "请先运行: python3 scripts/process_miniflux.py 或手动 fetch"
    exit 1
fi

# 检查是否有"已确认"标记
if ! grep -A2 "$URL" "$NOTES" | grep -q "已确认"; then
    echo "错误: 请先阅读文章内容，确认后再处理"
    echo "在 notes.md 中找到URL后，回复'可以处理'"
    exit 1
fi

# 执行实际处理
cd /root/mynews/scripts
python3 process_inbox.py "$@"
