---
name: struct-doc-answer
description: Use when creating, generating, or producing structured theoretical/knowledge documents in textbook format from any text content. Can accept raw text (articles, web pages, user input) directly without pre-formatting. Do not use for code generation or general Q&A. **This skill is designed to be delegated to a subagent when the user provides a URL input.**
---

# struct-doc-answer — 知识文档生成

## ⚠️ 核心原则

**唯一流程：process_inbox.py**。所有输入（URL 或纯文本）统一通过 `process_inbox.py` 处理，它内部完成：抓取 → 分类 → 创建本地文件 → hook 验证 → flomo 查重 → 上传/更新/跳过。

禁止绕开 process_inbox.py 直接调用 upload_flomo / memo_create。

---

## 一、输入类型与调用方式

### 1.1 URL 输入（微信公众号 / 网页）

```bash
cd /root/mynews/scripts && python3 process_inbox.py \
  --url "https://mp.weixin.qq.com/s/xxxx" \
  --domain "技术" \
  --subdomain "AI" \
  --title "知识点名称" \
  --tags "#信号笔记 #技术 #AI" \
  --ai-content "**概念**：<mark>核心概念</mark>定义...
**子概念**：
- <mark>要点一</mark>：..."
```

- `--url`：必填，文章链接
- `--domain`：必填，一级领域（技术/经济/政治/自然科学/社会科学等）
- `--subdomain`：必填，二级领域
- `--title`：必填，知识点名称（将作为文件名第三段）
- `--tags`：必填，≥3 个标签，第一行必须是 `#信号笔记` 或其他信号类型
- `--ai-content`：必填，AI 已生成的概念+子概念内容

### 1.2 纯文本输入（无 URL）

```bash
cd /root/mynews/scripts && python3 process_inbox.py \
  --content "原材料正文（用户输入的原始内容）" \
  --domain "教育科学" \
  --subdomain "家庭教育" \
  --title "知识点名称" \
  --tags "#信号笔记 #教育科学 #家庭教育" \
  --ai-content "**概念**：<mark>核心概念</mark>定义...
**子概念**：
- <mark>要点一</mark>：..."
```

- `--content`：原材料正文（用户输入的原始内容，不是 AI 加工后的内容）
- 其余参数同 URL 模式

---

## 二、文档结构（flomo 格式）

```markdown
#信号笔记 #技术 #AI

**技术_AI_知识点名称**

**来源**：来源（微信用发布账号，其他默认"网络"）

**概念**：<mark>核心概念</mark>精确定义。<mark>核心数据</mark>用高亮强调。

**子概念**：
- <mark>关键发现一</mark>：高亮要展示核心数据
- <mark>关键发现二</mark>：高亮关键实体名称
```

### 2.1 允许的 flomo 语法

| 语法 | 用途 |
|------|------|
| `**加粗**` | 标题/段落强调 |
| `<mark>高亮</mark>` | 关键术语、核心数据 |
| `- xxx` | 无序列表 |
| `1. xxx` | 有序列表 |

### 2.2 禁止的语法

`#` 标题 / `>` 引用 / ` ``` ` 代码块 / `[标题](url)` 链接 / `![图片](url)` / `---` 水平线 / `|` 表格

### 2.3 文件名格式

`领域_二级领域_知识点.md`，三段式，路径 `answers/领域/二级领域/文件名.md`（4 层）。

**文件名知识点部分禁止 `-`**：只允许 `中文/字母/数字/()/ .`，英文词汇用 `_` 分隔或合并。如 `WAIC2026重磅成果` 而非 `WAIC-2026-重磅成果`。

---

## 三、标签规则

- ≥3 个标签，必含 `#信号笔记`（或 `#趋势信号` / `#知识基座` 等信号类型）
- 微信文章：标签含领域，如 `#技术` `#AI`
- `--tags` 参数第一项即为信号类型标签

---

## 四、流程说明

| 步骤 | 内部自动完成 |
|------|------------|
| 1 | fetch_wechat_article 抓取（URL 模式）或使用 --content |
| 2 | 关键词分类确定 domain/subdomain |
| 3 | 创建本地文件 answers/领域/二级领域/文件名.md |
| 4 | pre-commit hook 验证格式 |
| 5 | search_flomo 查重（relevance ≥ 0.9 时显示新旧内容对比） |
| 6 | AI 判断：完全相同 → 跳过；新增内容 → update；完全不同 → 新建 |
| 7 | upload_flomo 或 update_flomo 上传 |

**relevance ≥ 0.9 时**：脚本会打印"已有笔记内容"和"新文章内容"对比，AI 必须自己判断有无实质新增内容，有新增必须 update_flomo，无新增才允许跳过。

---

## 五、操作边界

| 规则 |
|------|
| **只用 process_inbox.py**，禁止直接调 upload_flomo / memo_create |
| `--url` 模式自动抓取，无需手动 webfetch |
| `--ai-content` 是 AI 已生成的概念/子概念内容，不是原材料 |
| relevance ≥ 0.9 时必须对比新旧内容，禁止跳过有新增的条目 |
| 文件名知识点部分禁止连字符 `-` |
| hook 验证失败时脚本会报错，重试前需修复格式问题 |
