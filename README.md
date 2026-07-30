# mynews

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
       ├─ 假阳性（主题不同）→ --force-new 新建
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

## 使用方式

### CLI 全自动（推荐）

```bash
python3 process_inbox.py --auto --content "文章正文"
```

### CLI 手动精细控制

```bash
python3 process_inbox.py \
  --content "正文" \
  --domain "技术" --subdomain "AI" \
  --title "知识点名称" \
  --tags "#知识基座 #技术 #AI" \
  --ai-content "**概念**：<mark>核心概念</mark>定义..."
```

### Web UI

```bash
cd webui && python3 server.py
# 浏览器打开 http://localhost:8080
```

支持：粘贴正文、强制新建、三栏结果展示（笔记/对比/日志）。

## 参数说明

| 参数 | 说明 |
|------|------|
| `--content TEXT` | 原材料正文 |
| `--auto` | 全自动模式（AI 自行分析/分类/生成/决策） |
| `--domain`, `--subdomain` | 手动指定领域（自动模式下可选） |
| `--title NAME` | 知识点名称（自动模式下可选） |
| `--tags "T1 T2 T3"` | 标签，首位为信号类型（自动模式下可选） |
| `--ai-content MD` | 已生成的概念内容 |
| `--force-new` | 强制新建（跳过查重） |
| `--update MEMO_ID` | 增量更新已有笔记 |

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
│   └── process_inbox.py      # 核心处理器（全自动+手动）
├── webui/
│   ├── server.py             # Web UI 后端
│   └── index.html            # Web UI 前端（含夜色模式）
├── hooks/pre-commit          # flomo 格式验证 hook
├── struct-doc-answer/SKILL.md
├── .flomo_env                # flomo token（.gitignore 保护）
└── start-webui.bat           # 一键启动 Web UI
```

## 技术栈

- **AI 引擎**：本地 `kimi` CLI（`~/.kimi-code/bin/kimi`）
- **存储**：flomo API（MCP 协议）
- **前端**：纯 HTML + CSS + JS（无框架）
- **后端**：Python http.server（`ThreadingHTTPServer`）
