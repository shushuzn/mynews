#!/bin/bash
# 用 mimo 自动处理 _inbox 中的条目
# 逐条处理，每条调用 mimo headless

INBOX_DIR="/root/mynews/_inbox"
DONE_DIR="/root/mynews/_inbox_done"
MIMOCODE_HOME=$(mktemp -d)
LOG="/root/mynews/logs/inbox_mimo.log"

mkdir -p "$DONE_DIR" "$(dirname $LOG)"

FILES=$(ls "$INBOX_DIR"/*.md 2>/dev/null)
if [ -z "$FILES" ]; then
    exit 0
fi

# Track the current file being processed so the trap can access it
CURRENT_FILE=""
CURRENT_BASENAME=""

cleanup() {
    # Record what happened to the current file before exiting
    if [ -n "$CURRENT_FILE" ] && [ -f "$CURRENT_FILE" ]; then
        MIMO_EXIT=${MIMO_EXIT:-143}
        if [ "$MIMO_EXIT" -eq 0 ]; then
            echo "  [$CURRENT_BASENAME] Done (exit $MIMO_EXIT)" >> "$LOG"
            mv "$CURRENT_FILE" "$DONE_DIR/" 2>/dev/null
        else
            echo "  [$CURRENT_BASENAME] Failed (exit $MIMO_EXIT)" >> "$LOG"
        fi
    fi
    rm -rf "$MIMOCODE_HOME"
    exit 0
}

trap cleanup TERM INT

for f in $FILES; do
    CURRENT_FILE="$f"
    CURRENT_BASENAME=$(basename "$f")
    echo "[$(date)] Processing: $CURRENT_BASENAME" >> "$LOG"

    # 读取文件提取 URL
    URL=$(grep -A1 "^# SOURCE_URL" "$f" | tail -1 | tr -d '[:space:]')
    TITLE=$(grep -A1 "^# FEED_TITLE" "$f" | tail -1 | tr -d '[:space:]')

    if [ -z "$URL" ]; then
        echo "  No URL, moving to failed" >> "$LOG"
        mv "$f" "${f}.failed" 2>/dev/null
        CURRENT_FILE=""
        continue
    fi

    # 调用 mimo headless 处理
    PROMPT=$(mktemp -t mimo-inbox.XXXXXX)
    cat >"$PROMPT" <<PROMPT_EOF
你是一个微信/RSS文章处理助手。请处理以下文章，生成 flomo 笔记并上传。

URL: $URL
Feed: $TITLE

处理步骤：
1. 用 bash 执行: cd /root/mynews/scripts && python3 -c "from mynews_utils import fetch_wechat_article; t,s,e,wx=fetch_wechat_article('$URL',use_cache=False); print(f'标题: {wx}'); print(f'来源: {s}'); print(f'字数: {len(t)}'); print(t[:1500])" 获取文章内容
2. 理解文章，判断领域(domain)和二级领域(subdomain)，参考有效列表：
   - 社会科学: 军事历史, 社会治理, 政治学, 经济学, 教育, 法律, 哲学, 心理学
   - 技术: AI芯片, 大模型, 软件开发, 互联网, AI应用
   - 自然科学: 物理, 化学, 生物, 环境科学
   - 政治: 外交, 国际关系, 国防
   - 医学: 临床医学, 药物学, 公共卫生
   - 经济: 产业, 企业, 市场
   - 管理: 企业战略, 组织管理
   - 教育科学: 教育政策, 教育技术
   - 安全: 网络安全, 信息安全
   - 游戏: 游戏产业
3. 生成 flomo 内容（概念+子概念，用<mark>高亮关键词）——必须用自己的话综合概括原文意思，禁止复述或摘抄原文：
   **概念**：<mark>一句话概括的核心观点</mark>——用你自己的理解重新组织，融合多个信息点
   **子概念**：
   - <mark>要点A</mark>：用自己的话解释
   - <mark>要点B</mark>：用自己的话解释
   理解标准：能用自己的语言解释这篇文章"说明了什么"，而不是列点复述原文
4. 用 bash 执行 process_inbox.py 上传（--url --title --domain --subdomain --tags --ai-content 参数）：
   cd /root/mynews/scripts && python3 process_inbox.py --url "$URL" --domain "领域" --subdomain "二级领域" --title "知识点标题" --tags "#信号笔记 #领域 #二级领域" --ai-content "flomo内容"
5. 如果查重检测到高相似(relevance>=0.9)，程序会退出，你需要：
   a. 搜索现有内容确认是否同一篇文章
   b. 如果不是同一篇，直接调用 upload_flomo() 上传新 memo
   c. 清理本地文件
6. 标题用中文，禁止使用 -（英文缩写用连字符），知识点部分不能有下划线分隔

重要：来源行写成"作者（机构），《期刊》"格式，不要只写作者名。
PROMPT_EOF

    MIMOCODE_HOME="$MIMOCODE_HOME" /root/.mimocode/bin/mimo run --format json --dangerously-skip-permissions --dir /root/mynews < "$PROMPT" >> "$LOG" 2>&1 &
    MIMO_PID=$!

    wait $MIMO_PID
    MIMO_EXIT=$?
    rm -f "$PROMPT"

    if [ "$MIMO_EXIT" -eq 0 ]; then
        echo "  [$CURRENT_BASENAME] Done (exit $MIMO_EXIT)" >> "$LOG"
        mv "$f" "$DONE_DIR/" 2>/dev/null
    else
        echo "  [$CURRENT_BASENAME] Failed (exit $MIMO_EXIT)" >> "$LOG"
    fi
    CURRENT_FILE=""
done

rm -rf "$MIMOCODE_HOME"
