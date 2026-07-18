# mynews

AI 处理 RSS（通过 Miniflux）生成 flomo 格式笔记并上传到 flomo。

## 文档格式

```
#信号类型 #领域 #二级领域 #具体概念

**领域_二级领域_知识点**

**来源**：出处

**概念**：<mark>核心定义</mark>——正文

**子概念**：

- <mark>要点1</mark>：说明
- <mark>要点2</mark>：说明
```

标签行后无空行。高亮用 `<mark>`。

## 处理管道

```
Miniflux RSS ──→ process_miniflux.py ──→ _inbox/ ──→ 人工逐条处理 ──→ flomo
```

- **process_miniflux.py**：从 Miniflux API 拉取新条目写入 `_inbox`
- **process_inbox.py**：处理单条条目，验证格式，上传 flomo
- **process_inbox_mimo.sh**：调用 mimo headless 自动处理（需手动触发或配 cron）

## Cron 自动化

```bash
# 抓取 RSS（每5分钟）
*/5 * * * * cd /root/mynews && python3 scripts/process_miniflux.py >> logs/miniflux_cron.log 2>&1

# 自动处理 inbox（每10分钟，需先启动 mimo 服务）
*/10 * * * * /root/mynews/scripts/process_inbox_mimo.sh >> logs/inbox_mimo.log 2>&1
```

**推荐做法**：关闭自动处理 cron，改为人工逐条处理（`process_inbox.py --url ...`），质量更高。

## Miniflux 配置

在 `miniflux.env` 中配置：

```
MINIFLUX_URL=http://localhost:8080
MINIFLUX_API_KEY=your_api_key
```

导入 OPML：在 Miniflux UI → Settings → Subscriptions → Import OPML

## MCP 配置（flomo）

MCP 配置必须放在 `~/.kimi-code/mcp.json`（注意：不是 `~/.config/kimi-code/`）。

```json
{
  "mcpServers": {
    "flomo": {
      "type": "streamable-http",
      "url": "https://flomoapp.com/mcp",
      "headers": {
        "Authorization": "Bearer <你的 flomo token>"
      }
    }
  }
}
```

配置后**重启 session** 生效，用 `/mcp` 查看连接状态。

## 手动处理一条

```bash
cd /root/mynews/scripts
python3 process_inbox.py \
  --url "https://example.com/article" \
  --domain "技术" \
  --subdomain "AI应用" \
  --title "文章标题" \
  --tags "#信号笔记 #技术 #AI应用" \
  --ai-content '**概念**：<mark>核心概念</mark>——说明

**子概念**：

- <mark>要点1</mark>：说明'
```

## 首次安装

```bash
./setup_hooks.sh          # 安装 pre-commit hook
mkdir -p logs             # 日志目录
```

## 目录结构

```
mynews/
├── answers/                  # 本地草稿（.gitignore，不上传）
├── _inbox/                   # 待处理条目（抓取自 Miniflux RSS）
├── _inbox_done/              # 已处理
├── _inbox_failed/            # 处理失败
├── data/                     # 处理状态
├── logs/                     # cron 日志
├── scripts/
│   ├── process_miniflux.py   # 从 Miniflux 拉取条目
│   ├── process_inbox.py      # 处理 inbox 并上传 flomo
│   ├── process_inbox_mimo.sh # mimo 自动处理脚本
│   └── mynews_utils.py       # 工具函数
├── hooks/pre-commit          # flomo 格式验证 hook
└── struct-doc-answer/SKILL.md
```

## 格式规则

- **文件名**：3段式 `领域_二级领域_知识点`，禁止 `-` 连字符
- **路径**：4层 `answers/领域/二级领域/文件名.md`
- **高亮**：`<mark>` 用于概念核心词和子概念关键词
- **标题禁止连字符**：加粗标题中不可使用 `-`
- **标签**：第一行，≥3个，含 `#信号笔记`/`#趋势信号`/`#知识基座` 等
- **必须段落**：`**来源**：`、`**概念**：`、`**子概念**：`
