---
name: struct-doc-answer
description: Use when creating, generating, or producing structured theoretical/knowledge documents in textbook format from any text content. Can accept raw text (articles, web pages, user input) directly without pre-formatting. Do not use for code generation or general Q&A.
---

# struct-doc-answer

## ⚠️ 铁律（违反=失职）

1. **唯一入口 `process_inbox.py --auto`**，禁止直调 `upload_flomo` / `memo_create`
2. **正文必须通过 `--content` 传入**，脚本自身不抓取 URL
3. **`--ai-content` 只写 `**概念**`**（tag 行/标题/来源行由脚本自动拼接，写在 ai-content 里会触发校验失败）
4. **relevance ≥ 0.9 时**（`--auto` 模式）：脚本自动调用 AI 决策——有增量→自动 merge 后 `--update`、假阳性→ `--force-new`、无增量→skip
5. **relevance < 0.9**：脚本自动新建，AI 不插手（禁止 fetch_flomo_memo 复盘）
6. **提交/推送等用户批准**，禁止擅自 commit / push / rm

---

## 参数速查

| 参数 | 说明 |
|------|------|
| `--content TEXT` | 原材料正文 |
| `--domain`, `--subdomain` | 一级/二级领域 |
| `--title NAME` | 知识点名称（文件名一部分，禁用字符见下文） |
| `--tags "T1 T2 T3"` | ≥3 标签，首位信号类型（下节） |
| `--ai-content MD` | **仅写 `**概念**`** 段落，不含 tag/标题/来源 |
| `--force-new` | 假阳性时强制新建笔记 |
| `--update MEMO_ID` | 增量追加到已有笔记 |

---

## 操作流程（`--auto` 全自动）

一条命令完成全部流程：

```
1. 传入正文 → process_inbox.py --auto --content "正文"
2. 脚本调 kimi AI 分析全文
   → 自动分类 domain/subdomain
   → 生成 title、tags
   → 生成 **概念**（含 <mark>高亮</mark>）
3. hook 格式验证
4. search_flomo 查重
5a. relevance < 0.9 → 自动新建上传 ✅
5b. relevance ≥ 0.9 → AI 自动决策：
    ├── 假阳性 → --force-new 新建
    ├── 有增量 → 自动 merge 后 update
    └── 零增量 → skip 跳过
```

---

## 命令速查

```bash
cd /d/OpenClaw/mynews/scripts && python3 process_inbox.py \
  --auto --content "正文"
```

---

## 文档格式（flomo 笔记）

```
#信号笔记 #技术 #AI

**技术_AI_知识点名称**

**来源**：来源（微信用发布账号，其他默认"网络"）

**概念**：<mark>核心概念</mark>精确定义。
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

## 文件名规则

格式：`领域_二级领域_知识点.md`，路径 `answers/领域/二级领域/文件名.md`

文件名不设字符限制，任何字符均允许。

---

## 查重决策流程（`--auto` 模式）

```
process_inbox.py --auto 自动 call search_flomo
  │
  ├─ relevance < 0.9 → 自动新建（AI 不插手）
  │
  └─ relevance ≥ 0.9 → AI 自动决策
      ├─ 同主题 + 有增量 → 自动 merge 后 update
      ├─ 同主题 + 无增量 → skip
      └─ 假阳性（同关键词不同主题）→ --force-new 强制新建
```
