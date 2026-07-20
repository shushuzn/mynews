#!/bin/bash
# 用 mimo 自动处理 _inbox 中的条目
# 流程：mimo 理解文章 → 生成 ai-content → process_inbox.py --url 上传
# 改动：2026-07-20 重写，符合现在的 process_inbox.py + --ai-content-file + --force-new 流程

INBOX_DIR="/root/mynews/_inbox"
DONE_DIR="/root/mynews/_inbox_done"
FAILED_DIR="/root/mynews/_inbox_failed"
MIMOCODE_HOME="/root/.mimocode"
LOG="/root/mynews/logs/inbox_mimo.log"

mkdir -p "$DONE_DIR" "$FAILED_DIR" "$(dirname $LOG)"

shopt -s nullglob
FILES=("$INBOX_DIR"/*.md)
shopt -u nullglob

if [ ${#FILES[@]} -eq 0 ]; then
    exit 0
fi

# 一次 mimo 会话处理一个 inbox 文件
for f in "${FILES[@]}"; do
    BASENAME=$(basename "$f")
    echo "[$(date)] Processing: $BASENAME" >> "$LOG"

    # 提取 URL
    URL=$(awk '/^# SOURCE_URL/{getline; gsub(/[\r\n]/,""); print; exit}' "$f")
    FEED=$(awk '/^# FEED_TITLE/{getline; gsub(/[\r\n]/,""); print; exit}' "$f")

    if [ -z "$URL" ]; then
        echo "  No URL, skipping $BASENAME" >> "$LOG"
        mv "$f" "${f}.failed" 2>/dev/null
        continue
    fi

    # 构造 mimo prompt
    PROMPT=$(mktemp -t mimo-inbox.XXXXXX)
    cat >"$PROMPT" <<PROMPT_EOF
你是 RSS/微信公众号文章处理助手。处理以下 URL 的文章并上传到 flomo。

URL: $URL
Feed: $FEED

处理步骤：

1. 抓取文章内容。微信公众号用 mynews_utils.fetch_wechat_article；其他 URL 直接用 curl 抓 HTML 后用 BeautifulSoup 提取正文。
   - 微信公众号抓取命令：
     cd /root/mynews/scripts && python3 -c "from mynews_utils import fetch_wechat_article; t,s,e,wx=fetch_wechat_article('$URL',use_cache=False); print(f'## 标题: {wx}'); print(f'## 来源: {s}'); print('## 正文:'); print(t)"
   - 其他 RSS/网页：直接用 curl + 简单 HTML 解析

2. 阅读文章，用自己的话综合概括，生成 flomo 内容（**禁止复述或摘抄原文**，必须理解后重写）：
   - **概念**：<mark>一句话核心观点</mark>——用自己的话重新组织
   - **子概念**：
     - <mark>要点A</mark>：用自己的话解释
     - <mark>要点B</mark>：用自己的话解释
   - 标签：第一行 #信号笔记 #领域 #二级领域（≥3 个）

3. 判断领域：
   - 一级领域 domain：技术 / 经济 / 政治 / 自然科学 / 社会科学 / 医学 / 教育科学 / 安全 / 法律 / 游戏 / 管理
   - 二级领域 subdomain：常见的有 AI / 大模型 / AI芯片 / 软件开发 / 产业 / 市场 / 资本 / 心理学 / 历史 / 哲学 / 文学 / 语言 / 能源 / 外交 / 纪检监察 / 学习方法 / 健康 / 安全 等
   - 若不确定，可用 --domain 技术 --subdomain 软件开发 作为默认

4. 用 process_inbox.py 上传（这是唯一入口，不要直接调 upload_flomo）：

   # 先把 ai-content 写到临时文件，再用 --ai-content-file 传入
   AI_CONTENT_FILE=$(mktemp /tmp/ai_content.XXXXXX.md)
   cat > "\$AI_CONTENT_FILE" <<'AI_CONTENT_EOF'
<概念>...<子概念>...
AI_CONTENT_EOF

   cd /root/mynews/scripts && python3 process_inbox.py \\
     --url "$URL" \\
     --domain "<领域>" \\
     --subdomain "<二级领域>" \\
     --title "<知识点中文标题，三段式>" \\
     --tags "#信号笔记 #<领域> #<二级领域>" \\
     --ai-content-file "\$AI_CONTENT_FILE"

   rm -f "\$AI_CONTENT_FILE"

5. 关键规则：
   - **禁止直接调 upload_flomo**：必须通过 process_inbox.py（它内置查重 + 校验）
   - **文件名规则**：领域_二级领域_知识点.md 三段式，知识点部分禁止 "-" 连字符，英文用 "_" 分隔；不要用 - 替代 _（之前规则已废）
   - **来源**：微信文章来源用 process_inbox.py 自动从 HTML 提取的发布账号；其他 URL 默认"网络"
   - **查重处理**：若 process_inbox.py 检测到 relevance ≥ 0.9 高相似会停下并显示新旧内容对比——你需要判断：
     a. 内容确实重复 → 跳过（脚本本身已打印"已处理"，无需额外操作）
     b. 内容是假阳性（已有笔记和当前文章主题不同）→ 重跑加 --force-new：
        AI_CONTENT_FILE=\$(mktemp /tmp/ai_content.XXXXXX.md)
        # (写入 ai-content 到 \$AI_CONTENT_FILE)
        cd /root/mynews/scripts && python3 process_inbox.py --url "$URL" --domain "..." --subdomain "..." --title "..." --tags "..." --ai-content-file "\$AI_CONTENT_FILE" --force-new
        rm -f "\$AI_CONTENT_FILE"
     c. 新文章对已有 memo 有实质增量 → 用 update_flomo(id, new_content) 整合（旧 ID 从脚本打印中获取）
   - **flomo 内容中禁止 URL**：纯文本 https?:// 链接和 🔗+URL 都禁止

6. 处理完成后，inbox 文件会自动由 process_inbox.py 移动到 _inbox_done/。你只需执行 step 4 的命令即可。

完成后回复"处理完成"。
PROMPT_EOF

    MIMOCODE_HOME="$MIMOCODE_HOME" /root/.mimocode/bin/mimo run --format json --dangerously-skip-permissions --dir /root/mynews < "$PROMPT" >> "$LOG" 2>&1
    MIMO_EXIT=$?
    rm -f "$PROMPT"

    if [ "$MIMO_EXIT" -eq 0 ]; then
        # 处理成功后，inbox 文件已被 process_inbox.py 移动到 _inbox_done
        if [ -f "$f" ]; then
            # mimo 报告成功但文件还在 inbox（可能 mimo 跳过或报错但 exit 0），手动移走
            echo "  [$BASENAME] Done (exit $MIMO_EXIT) but file still in inbox, moving" >> "$LOG"
            mv "$f" "$DONE_DIR/" 2>/dev/null
        else
            echo "  [$BASENAME] Done (exit $MIMO_EXIT)" >> "$LOG"
        fi
    else
        echo "  [$BASENAME] Failed (exit $MIMO_EXIT)" >> "$LOG"
        if [ -f "$f" ]; then
            mv "$f" "${FAILED_DIR}/" 2>/dev/null
        fi
    fi
done
