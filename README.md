# mynews

[![CI](https://github.com/shushuzn/mynews/actions/workflows/test.yml/badge.svg)](https://github.com/shushuzn/mynews/actions/workflows/test.yml)

AI 内容处理管道：输入正文 → 自动分析 → 生成 flomo 格式笔记 → 上传 flomo。

## 快速开始

```bash
# 一行命令全自动处理
cd scripts && python3 process_inbox.py --auto --content "你的正文"

# 或启动 Web UI
cd webui && python3 server.py
# 打开 http://localhost:8080
```

`--auto` 模式自动完成：AI 分析内容 → 分类领域 → 生成标题/标签/概念 → 查重去重 → 假阳性/增量判断 → 上传/更新/跳过。

## 工作流

```
你输入正文
  → [--auto] 调用 kimi AI 分析
    → 自动分类 domain/subdomain
    → 生成标题、标签
    → 生成 **概念**（含 <mark>高亮</mark>）
  → hook 格式校验
  → search_flomo 查重
    ├─ relevance < 0.9 → 自动新建
    └─ relevance ≥ 0.9 → AI 决策
       ├─ 假阳性（主题不同）→ 强制新建
       ├─ 有增量（主题相同但有新内容）→ AI 合并后 update
       └─ 真重复（无增量）→ skip 跳过
  → 上传 flomo
```

## 安装

```bash
./setup_hooks.sh          # 安装 pre-commit hook（flomo 格式校验）
```

**环境变量**（二选一）：

1. 设置系统环境变量：`setx FLOMO_TOKEN "你的flomo_token"`（Windows）
2. 或创建 `.flomo_env` 文件（已加入 `.gitignore`）：
   ```
   FLOMO_TOKEN=你的flomo_token
   ```

**可选配置**：

| 变量 | 默认 | 说明 |
|------|------|------|
| `MYNEWS_RSS_INTERVAL` | `180` | 后台拉取/处理间隔（秒），最低 30 |
| `MYNEWS_RSS_ONLY` | `""` | 硬过滤：设为某源 URL 时只抓取该源（精确匹配） |
| `MYNEWS_SKIP_KIMI_CHECK` | `""` | 设为 `1` 跳过 kimi 远端版本检查（离线环境提速） |
| `OPML_PATH` | `rss_sources.opml` | RSS 源列表路径 |

## 使用方式

### CLI 全自动（推荐）

```bash
python3 process_inbox.py --auto --content "文章正文"
```

超长文本（>2 万字符，避免 Windows 命令行长度限制）推荐从 stdin 传入：

```bash
cat article.txt | python3 process_inbox.py --auto --content-stdin
```

### 非交互式批量处理

从 `rss_sources.opml` 中的所有 RSS 源循环抓取最新条目，自动处理直到全部完成：

```bash
python3 scripts/auto_process.py              # 处理全部（跳过已处理的）
python3 scripts/auto_process.py --limit 20  # 最多处理 20 条
python3 scripts/auto_process.py --force      # 忽略已处理记录，强制处理全部
python3 scripts/auto_process.py --delay 5    # 每条间隔 5 秒
```

### CLI（指定参数覆盖 AI 结果）

```bash
python3 process_inbox.py --auto --content "正文" \
  --domain "技术" --subdomain "AI" \
  --tags "#知识基座 #技术 #AI"
```

### Web UI + 服务端后台全自动

```bash
cd webui && python3 server.py
# 浏览器打开 http://localhost:8080
```

**服务端后台（无需浏览器）**：启动后每 3 分钟自动刷新 RSS 缓存（供前端列表展示），不自动处理条目；处理动作由前端 ⚡ 模式或 CLI 触发。

**前端**：正文 / URL（自动抓取+预览）/ 图片三种输入；AI 自动分析、查重、上传/更新/跳过；自动处理模式（⚡ 闪电按钮：从 RSS 源循环处理最新未处理链接，全部处理完每 3 分钟自动重查）；刷新按钮（强制绕过缓存立即拉取 RSS）；三栏结果展示（笔记/对比/日志）；侧边栏统计、最近处理记录（本地保存、可导出/导入备份）；三态主题（浅色/深色/跟随系统）、快捷键（`?` 查看）。

**已处理记录共享**：服务端存 `scripts/.auto_processed.json`，前端处理成功后同步到服务端、处理前合并服务端记录——前端 ⚡ 与后台线程不会重复处理同一条。

## 参数说明

| 参数 | 说明 |
|------|------|
| `--content TEXT` | 原材料正文（`--content` 与 `--content-stdin` 必填其一） |
| `--content-stdin` | 从 stdin 读取原材料正文（超长文本推荐，无命令行长度限制） |
| `--auto` | 全自动模式（AI 自行分析/分类/生成/决策，默认行为） |
| `--domain`, `--subdomain` | 可选：覆盖 AI 生成的领域 |
| `--tags "T1 T2 T3"` | 可选：覆盖 AI 生成的标签（首位为信号类型） |

## 文档格式（flomo 笔记）

```
#知识基座 #技术 #AI

**技术_AI_知识点名称**

**来源**：网络

**概念**：<mark>核心概念</mark>精确定义。
```

### 允许语法
`**加粗**` / `<mark>高亮</mark>` / `- 无序列表` / `1. 有序列表`

### 禁止语法
`# 标题` / `> 引用` / `` ``` `` 代码块 / `[链接](url)` / `![图片]` / `---` 水平线 / `|` 表格

## 信号类型标签

| 标签 | 适用场景 |
|------|---------|
| `#知识基座` | 概念/定理/机制（客观知识） |
| `#趋势信号` | 正在发生的结构性变化 |
| `#信号笔记` | 单次事件/数据点 |
| `#分析框架` | 可复用的方法论 |
| `#知识载体` | 工具/资源/数据集 |

## 标题规则

- 格式：`领域_二级领域_知识点`，**恰好 2 个下划线**
- title 不能包含下划线、空格、半角斜杠、加号、全角冒号、en-dash
- hook 强制执行，不符合的字符必须替换

## 目录结构

```
mynews/
├── answers/                  # 本地草稿（.gitignore）
├── data/                     # 处理状态（reviewed_pass.json 等）
├── scripts/
│   ├── process_inbox.py      # 核心处理器（全自动+手动）
│   ├── auto_process.py       # 非交互式批量处理器
│   ├── rss_utils.py          # RSS/网页抓取共享模块
│   ├── check_rss_health.py   # RSS 源健康检查工具
│   └── check_frontend_js.mjs # 前端 JS 语法检查（CI/本地复用）
├── tests/                    # 单元测试（python -m unittest discover -s tests，64 用例）
│   ├── test_rss_utils.py
│   ├── test_process_inbox.py
│   ├── test_auto_process.py
│   ├── test_server.py
│   └── test_check_rss_health.py
├── rss_sources.opml          # RSS 源列表（152 个 feed）
├── webui/
│   ├── server.py             # Web UI 后端
│   └── index.html            # Web UI 前端（含夜色模式）
├── hooks/pre-commit          # flomo 格式验证 hook
├── struct-doc-answer/SKILL.md
├── .flomo_env                # flomo token（.gitignore 保护）
└── start-webui.bat           # 一键启动 Web UI
```

### 运行测试

```bash
python -m unittest discover -s tests -v     # Python 单元测试（64 用例）
node scripts/check_frontend_js.mjs          # 前端 JS 语法检查
```

### RSS 源健康检查

```bash
python scripts/check_rss_health.py              # 全部检查，输出摘要
python scripts/check_rss_health.py --failed-only  # 只显示失效/异常的源
python scripts/check_rss_health.py --verbose     # 显示每个源的状态
```

## 技术栈

- **AI 引擎**：本地 `kimi` CLI（`~/.kimi-code/bin/kimi`）
- **存储**：flomo API（MCP 协议）
- **前端**：纯 HTML + CSS + JS（无框架）
- **后端**：Python http.server（`ThreadingHTTPServer`）
