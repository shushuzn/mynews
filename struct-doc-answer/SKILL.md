---
name: struct-doc-answer
description: Use when creating, generating, or producing structured theoretical/knowledge documents in textbook format from any text content. Can accept raw text (articles, web pages, user input) directly without pre-formatting. Do not use for code generation or general Q&A. **This skill is designed to be delegated to a subagent when the user provides a URL input.**
---

# struct-doc-answer

## ⚠️ 铁律（违反=失职）

1. **唯一入口 `process_inbox.py`**，禁止直调 `upload_flomo` / `memo_create`
2. **微信公众号必须 `fetch_wechat_article(use_cache=False)` 抓最新内容**（禁用缓存）
3. **`--ai-content` 只写 `**概念**` + `**子概念**`**（tag 行/标题/来源行由脚本自动拼接，写在 ai-content 里会触发校验失败）
4. **relevance ≥ 0.9 时**：必须比对脚本打印的旧/新内容做决策——有增量→`--update OLD_ID`、假阳性→`--force-new`、无增量→skip
5. **relevance < 0.9**：脚本自动新建，AI 不插手（禁止 fetch_flomo_memo 复盘）
6. **提交/推送等用户批准**，禁止擅自 commit / push / rm

---

## 参数速查

| 参数 | 说明 |
|------|------|
| `--url URL` | 文章链接（脚本内部抓取，默认缓存） |
| `--content TEXT` | 原材料正文（与 `--url` 二选一） |
| `--domain`, `--subdomain` | 一级/二级领域 |
| `--title NAME` | 知识点名称（文件名一部分，禁用字符见下文） |
| `--tags "T1 T2 T3"` | ≥3 标签，首位信号类型（下节） |
| `--ai-content MD` | **仅写 `**概念**` + `**子概念**`** 两段，不含 tag/标题/来源 |
| `--force-new` | 假阳性时强制新建笔记 |
| `--update MEMO_ID` | 增量追加到已有笔记 |

---

## 操作流程（三步）

```
Step 1 — 抓取：fetch_wechat_article(use_cache=False)   （仅微信公众号）
Step 2 — 构造：AI 在上下文内写出完整 ai-content（**概念** + **子概念** + mark 高亮）
Step 3 — 上传：process_inbox.py --url / --content（含查重+relevance 决策）
```

**Step 3 内部流程**：脚本自动抓取→创建本地文件→hook 校验格式→search_flomo 查重→
- relevance < 0.9 → 自动新建
- relevance ≥ 0.9 → 打印新旧内容，AI 比对后决定 `--update` / `--force-new` / skip

---

## 命令速查

```bash
# URL 模式
cd /root/mynews/scripts && python3 process_inbox.py \
  --url "https://..." \
  --domain "技术" --subdomain "AI" \
  --title "知识点名称" \
  --tags "#信号笔记 #技术 #AI" \
  --ai-content "**概念**：<mark>核心概念</mark>定义...\n**子概念**：\n- <mark>要点一</mark>：...\n- <mark>要点二</mark>：..."

# 纯文本模式（--url 换成 --content "原材料正文…"，其余相同）
```

---

## 文档格式（flomo 笔记）

```
#信号笔记 #技术 #AI

**技术_AI_知识点名称**

**来源**：来源（微信用发布账号，其他默认"网络"）

**概念**：<mark>核心概念</mark>精确定义。

**子概念**：
- <mark>关键发现一</mark>：高亮核心数据
- <mark>关键发现二</mark>：高亮关键实体
```

### 允许语法
`**加粗**` —标题/强调　`<mark>高亮</mark>` —关键术语/核心数据　`- xxx` / `1. xxx` —列表

### 禁止语法
`# 标题` / `> 引用` / `` ``` `` 代码块 / `[标题](url)` 链接 / `![图片](url)` / `---` 水平线 / `|` 表格

---

## 标签规则

### 信号类型（五选一，根据核心内容判断）

| 标签 | 适用场景 | 误用示例 |
|------|---------|---------|
| `#知识基座` | 概念/定理/机制 | 衍射极限→用 `#信号笔记` ❌ |
| `#趋势信号` | 正在发生的结构性变化 | 玻璃基封装→用 `#信号笔记` ❌ |
| `#信号笔记` | 单次事件/数据点 | — |
| `#分析框架` | 可复用的方法论 | 行动优于纠结→用 `#信号笔记` ❌ |
| `#知识载体` | 工具/资源/数据集 | — |

### 其他规则
- ≥3 标签，**信号类型为首项**
- 微信文章：带领域标签如 `#技术` `#AI`

---

## 文件名禁用字符（hook 强制执行）

格式：`领域_二级领域_知识点.md`，路径 `answers/领域/二级领域/文件名.md`

`--title` 中必须替换以下 6 类，否则 hook 直接失败：

| 禁用 | 替代 | 示例 |
|------|------|------|
| 半角空格 | `_` | `WAIC 2026`→`WAIC_2026` |
| `/` 斜杠 | 合并 | `NPO/CPO`→`NPOCPO` |
| `+` 加号 | `加` | `1+1`→`1加1` |
| `：` 全角冒号 | `-` | `标题：副标题`→`标题-副标题` |
| `–` en-dash | `-` | `M–O–Si`→`M_O_Si` |
| 连续 `_`+字母数字 | 合并或加字母 | `RISC_V`→`RISCV`；`M_O_Si`→`MOSi` |

---

## 查重决策流程

```
process_inbox.py 自动 call search_flomo
  │
  ├─ relevance < 0.9 → 自动新建（AI 不插手）
  │
  └─ relevance ≥ 0.9
      ├─ 脚本打印旧内容 + 新内容
      ├─ AI 比对：
      │   ├─ 同主题 + 有增量 → --update OLD_ID
      │   ├─ 同主题 + 无增量 → skip
      │   └─ 假阳性（同关键词不同主题）→ --force-new
      │
      └─ 假阳性时必须先 fetch_flomo_memo 读旧笔记确认主题不同，再用 --force-new
```
