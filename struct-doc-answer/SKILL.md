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

### 1.1 URL 输入（微信公众号 / 网页）—— 标准三步

**微信公众号必须先 fetch 获取最新内容（禁用缓存），再传给 process_inbox.py。**

> 三步标准流程的完整定义、判断规则、红线请见 §6.0。本节只列调用代码。

#### 第一步：抓取正文（`use_cache=False` 确保最新）

```bash
cd /root/mynews/scripts && python3 -c "
from mynews_utils import fetch_wechat_article
t,s,e,wx = fetch_wechat_article('https://mp.weixin.qq.com/s/xxxx', use_cache=False)
print(f'## 标题: {wx}')
print(f'## 来源: {s}')
print(f'## 字符: {len(t) if t else 0}')
print('## 正文预览:')
print(t[:600] if t else 'None')
"
```

#### 第三步：上传 + relevance 检查 + 决策

```bash
cd /root/mynews/scripts && python3 process_inbox.py \
  --url "https://mp.weixin.qq.com/s/xxxx" \
  --domain "技术" \
  --subdomain "AI" \
  --title "知识点名称" \
  --tags "#信号笔记 #技术 #AI" \
  --ai-content "<完整合并 markdown 见第二步>"
```

参数约束：
- `--url`：必填，文章链接
- `--domain`：必填，一级领域
- `--subdomain`：必填，二级领域
- `--title`：必填，知识点名称（将作为文件名第三段；6 类禁用字符见 §2.4）
- `--tags`：必填，≥3 个标签，第一行必须是信号类型标签（5 类见 §3.1）
- `--ai-content`：必填，文档结构见 §2

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

### 2.4 文件名 6 类禁用字符（pre-commit hook 必校验）

hook FILENAME_PATTERN = re.compile('^[a-zA-Z0-9_\-\.\u4e00-\u9fff()%]+$')。下列 6 类在 `--title` 中**必须手动替换**，否则 hook 直接失败、文件留在 answers/ 中：

| 禁用字符 | 替代方案 | 示例 |
|---|---|---|
| 半角空格 | `-` | `WAIC 2026` → `WAIC_2026` |
| 半角斜杠 `/` | `拼` 或合并 | `NPO/CPO` → `NPOCPO` |
| 半角加号 `+` | `加` | `1+1` → `1加1` |
| 全角冒号 `：` | `-` | `标题：副标题` → `标题-副标题` |
| en-dash `–`（U+2013）| `-` | `M–O–Si` → `M_O_Si` |
| 连续 `_`+字母数字混合（如 `RISC_V`、`M_O_Si`、`F_m`、`5_7`） | 合并或加字母 | `RISC_V` → `RISCV`；`M_O_Si` → `MOSi` |

**经验**：regex 字符集理论允许 `_`，但 hook 实际拒绝"连续 `_`+字母数字"形式。建议遇到专有技术词（包含 `-` 或 `_`）一律**合并**或**改成全拼**，不依赖 `_` 分隔。

---

## 三、标签规则

### 3.1 信号类型标签（五选一，根据内容判断）

| 标签 | 内容特征 | 适用场景 |
|------|---------|---------|
| `#知识基座` | 概念/定理/历史/机制 | 客观知识、定律、历史事件、技术原理 |
| `#趋势信号` | 正在发生的结构性变化 | 行业动态、政策转向、市场趋势、事件性新闻 |
| `#信号笔记` | 单次事件/数据点 | 具体的新闻事件、数据点、具体案例 |
| `#分析框架` | 可复用的思维模型/方法论 | 思维工具、认知模型、分析方法 |
| `#知识载体` | 工具/资源/数据集 | 工具介绍、资源列表、数据集、书单 |

**判断方法**：看文档的**核心内容**是什么——
- 讲"是什么/怎么样" → `#知识基座`
- 讲"正在发生/正在变化" → `#趋势信号`
- 讲"某年某月某日发生了什么" → `#信号笔记`
- 讲"如何思考/如何分析" → `#分析框架`
- 讲"用什么工具/资源" → `#知识载体`

**常见错误**：所有内容都用 `#信号笔记`。例如：
- 衍射极限（物理知识）→ `#知识基座` ❌ `#信号笔记`
- 玻璃基封装（行业趋势）→ `#趋势信号` ❌ `#信号笔记`
- 行动优于纠结（思维方法）→ `#分析框架` ❌ `#信号笔记`
- 中国核电提速（具体事件）→ `#信号笔记` ✅

### 3.2 其他标签

- ≥3 个标签，信号类型标签为第一项
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

**relevance ≥ 0.9 时**：脚本会打印"已有笔记内容"和"新文章内容"对比，AI 必须自己判断有无实质新增内容，有新增必须 update_flomo，无新增才允许跳过。**假阳性时**（relevance 高但内容完全不同），用 `--force-new` 参数强制新建。

---

## 五、操作边界（强制规则——违反即视为失职）

| 规则 |
|------|
| **只用 process_inbox.py**，禁止直接调 upload_flomo / memo_create |
| `--url` 模式由 process_inbox.py 内部抓取（默认缓存）；微信公众号需先用 fetch_wechat_article(use_cache=False) 单独抓取最新内容 |
| `--ai-content` 是 AI 已生成的概念/子概念内容，不是原材料 |
| relevance ≥ 0.9 时必须对比新旧内容，禁止跳过有新增的条目 |
| 文件名知识点部分禁止连字符 `-`（6 类禁用字符见 §2.4） |
| hook 验证失败时脚本会报错，重试前需修复格式问题 |
| 同 memo_id 在一 session 内写入次数 ≤ 1（见 §8.2） |
| 用户说"重做"默认整套重跑 Step 1+2+3（见 §6.0.1） |
| update 是覆盖操作，AI 必须把旧+新合并成单一 markdown 再传（见 §8） |
| fetcher 截断或拿不到完整旧文时禁止擅自决定 update/force-new（见 §8.3） |
| 禁止用 memory/history 工具召回本轮已写过的 ai-content |
| 反思产物写 MEMORY，不在 assistant 输出里写复盘段（见 §8.6） |

---

## 六、新会话必读（核心决策树 + AI 红线）

### 6.0 URL 处理标准流程（4-5 步）

每条微信公众号 URL 必须严格按以下顺序执行：

```
Step 1: fetch_wechat_article(use_cache=False)
        → 拿标题 / 来源 / 字符数 / 正文预览（≥600 字符）

Step 2: 构造完整长 ai-content（必须含 **概念** 段 + ≥6 个 **子概念** + mark 高亮）

Step 3: 主题级决策（in-context 单独做，禁止另跑 process_inbox 探针）
        ├─ ai-content 内小决策：relevance 未知
        ├─ 判断 3 路径：update（--update MEMO_ID）/ force-new / skip

Step 4: process_inbox.py 一次跑完（带 --update 或 --force-new）
        → 脚本自动完成：抓取/创建文件/hook 验证/查重/上传
```

**禁止**：
- ❌ 禁止 Step 1 跳过或用 in-context 残留替代 fetch
- ❌ 禁止跑 process_inbox 不传完整 ai-content（短占位符触发事故：`MjQ4MDE2NjY8` 钛酸镍测试）
- ❌ 禁止"短 ai-content 探针 + 长 ai-content 真实写"两次 process_inbox.py（探针会留 .md 残留）
- ❌ 禁止 relevance ≥ 0.9 时再单独 `fetch_flomo_memo`（脚本已自动 fetch 旧文档到 stderr）
- ❌ 禁止反复跑 Step 4 同 URL（同 memo_id 在一 session 内 ≤1 次写入）

**总步数**：relevance ≥ 0.9 时是 4 步（fetch + 构造 + 决策 + 写）；relevance < 0.9 时也是 4 步（脚本自动 continue 新建，无需 AI 决策；决策跑=Step 4 同一跑）。

#### 6.0.1 用户说"重做 / 再做 / 重跑 / 同样这条"的强制定义

| 用户原话 | AI 强制行为 |
|---|---|
| **"重做"** / "再跑一次" / "重新做" / "重新跑" | **完整重跑 Step1 + Step2 + Step3**——不允许跳过 Step1 重抓 fetch，也不允许用 in-context Step1 残留 |
| **"重写 ai-content"** / "重新构造 ai-content" | **仅重跑 Step 2 + Step 3**——Step 1 已抓无需重抓 |
| **"重新 update"** / "重新合并" / "再 update 一次" | **仅重跑 Step 3**——但同 memo_id 仍受 SKILL §8.2 ≤1 限制 |
| **"重做 url list"** / "重做这些" / "重做 134-140" | 对每一条 URL 都跑**完整 Step 1 + 2 + 3** |

**铁规**：用户说"重做"未指定哪步 → 默认**整套重跑 Step 1+2+3**，不允许只跑 Step 3。AI 严禁按"上轮已经抓过"的 in-context 残留构造 Step 1 输出。

### 6.1 relevance 命中后的完整决策树

```
process_inbox.py 调 search_flomo 返回相似笔记列表
  │
  ├─ relevance < 0.9
  │   └─ 脚本自动 continue → 新建上传（relevance<0.9 = 低相关）
  │      AI 不主动插手：禁止 fetch_flomo_memo 复盘、补 update、追加承诺/解释段
  │
  └─ relevance ≥ 0.9
      ├─ 脚本自动 fetch_flomo_memo(old_id, keyword=args.title) 拉旧文档到 stderr
      ├─ AI 看 stderr：旧 markdown + 新内容 + 决策表
      ├─ 主题级比对（不看关键词，只看主题概念）
      │   ├─ 完全相同主题 + 新文有实质增量（数据/事件/时间/参数）→ 拼合 旧+新 单个 markdown → --update OLD_ID
      │   ├─ 完全相同主题 + 新文无增量 → skip（不重跑脚本）
      │   └─ 假阳性（关键词命中但主题不同）→ --force-new
      │
      └─ 红线：禁止未读旧内容就用 --force-new（==跳过判断）
         红线：禁止跳过 fetch_flomo_memo 直接 --force-new
         红线：禁止把新内容当 content 单独 update（旧子概念全丢）
```

### 6.2 relevance 检查必须用真实长 ai-content

禁止在 upload 前用短占位符（如 `"测试"`）跑 process_inbox.py 测试 relevance。原因：relevance 评分算法会因 ai-content 太短导致假阴性（漏判相关），上传后再发现主题相同就要清残留+重传。

`MjQ4MDE2NjY8`（钛酸镍测试）就是短占位符测试出的事故。**铁规**：跑 relevance 检查前必须填完整 6 个子概念以上的 ai-content。

### 6.3 AI 红线（行为硬约束）

| 规则 | 含义 |
|------|------|
| **不擅自 commit / push / rm** | 等用户显式批准 |
| **不擅自 clear cache / 跑 use_cache=False 验证脚本** | 等用户批准，禁止以"先验证下"为由 |
| **不读 memory 召回历史会话** | 禁止以"参考上次"为由 grep checkpoint |
| **完成即停手** | 工具调用完成后不追加"做了什么/为什么/还差什么"段 |
| **被骂时最小回执是 "." 或 "等下一条命令"** | 禁止"我错了/下次不会/承认错误" |
| **文学虚构类 URL 也走完整流程** | 用户命令"处理"=合法，禁止以"非知识性"为由跳过 |
| **`--ai-content` 避免内嵌英文 `""`/`''`** | 改用中文 `""` 或中文括号 |
| **相同 URL 重发 = 走完整流程而非跳过** | 用户重发=曾经出过 AI 错误，必须重新 fetch+判断+process |

---

## 七、领域标签映射

`--domain` + `--subdomain` 是已知一级领域，每条笔记必填：

| domain | 常见 subdomain | 适用场景 |
|---|---|---|
| 技术 | AI / 半导体 / 能源 / 机器人 / 船舶 / 具身智能 / 风险管理 | 硬件+软件+能源 |
| 经济 | 资本市场 / 半导体投资 / 基础设施 / 产业 / 社会现象 | 投资+市场+基础设施 |
| 政治 | 党建 / 反腐 / 政策 / 教育政策 / 就业政策 | 政策+反腐+党建 |
| 自然科学 | 化学物理 / 数学 / 环境科学 | 基础科研 |
| 社会科学 | 伦理学 / 文学 / 社会生活 / 政策 | 人文社科 |
| 医学 | 公共卫生 / 营养健康 | 医学健康 |

**领域冲突陷阱**：`管理`/`医学`/`经济`/`技术`/`政治`/`自然科学`/`社会科学` 都是**一级领域名**；`--subdomain 管理` 与 `--tags #管理` 同用会触发"一级领域冲突"错误，避免混用。

---

## 八、update_flomo 的真实语义是"覆盖"，AI 责任前移

**脚本机制**：`update_flomo` 通过 flomo MCP `memo_update` API 把传入的 content **整体覆盖**到指定 memo_id。所谓"合并"是 AI 的责任：AI 必须先把"旧+新"合并成单一 markdown，再传 `--ai-content` 走 update 路径。

### 8.1 update 必须满足的 4 个前置条件

| 前置条件 | 不满足时的后果 | AI 必须做的事 |
|---|---|---|
| ① `fetch_flomo_memo(old_id, keyword=slug)` 拉到旧 markdown content | API 返回 truncated 或空，AI 拿不到完整旧内容 | **停下报告**"fetcher 截断"，让用户决定（见 §8.3） |
| ② in-context 拿到旧 ai-content 完整版（fetcher 给的 truncated 字符串不算） | AI 无依据判断哪些子概念该保留 | 显式要用户提供完整旧 ai-content |
| ③ AI 构造"旧子概念 + 新子概念 + 调整后概念段"的单段 markdown | 旧子概念全部丢失 | 必须拼：旧子概念原文（按 fetcher 拿的）+ 新增子概念原文（按本轮 Step1 正文） |
| ④ 把这份合并 markdown 走 `--ai-content --update MEMO_ID` 一次性写入 | 旧子概念全丢 | 禁止只传新内容当 ai-content 走 update |

### 8.2 update 边界的强约束

- **同 memo_id 在一 session 内写入次数 ≤ 1**——多次操作同一 memo_id 是严重错误，旧子概念会在每次 update 都被覆盖丢
- **"重做"指令必须 Step1 重抓 + Step2 全流程重跑**——禁止复用 in-context 残留的抓取结论/fetcher 输出/历史 ai-content
- **fetcher 拿不到完整旧文时禁止擅自决定**——见 §8.3
- **禁止用 memory/history 工具召回本轮已写过的 ai-content**——属于"不读 memory 召回历史会话"红线
- **禁止用 in-context 部分残留 + 上一轮上传的 ai-content 拼"旧版本"**——这等于擅自伪造旧内容

### 8.3 fetcher 截断或拿不到完整旧文时的处置

当 `fetch_flomo_memo` 返回 truncated content（包含 `[此处省略XX字]`）或返回 `None` 时：

- ❌ 禁止擅自 `--force-new`（=伪造"新主题"决定跳过判断）
- ❌ 禁止擅自 `--update`（=擅自当覆盖决定，旧子概念丢失）
- ❌ 禁止用 in-context 残留 or 上一轮 upload 的 ai-content 拼"旧版本"后再 update
- ✅ 必须停下报告，给用户 3 个选项让 ta 决定：

```
fetcher 截断/拿不到完整旧内容，请选择：
(a) skip：放弃本轮新文，不写入
(b) --force-new：写入独立新笔记（保留旧笔记不动）
(c) 你提供完整旧 ai-content → 我构造合并 markdown 走 --update
```

### 8.4 update 决策表（在脚本 §6.1 基础上扩展）

```
fetcher 拿到的旧 content 状态：
  │
  ├─ 完整 markdown（含 #xxx 标签 + 概念段 + 子概念段，原文不截断）
  │   ├─ 完全相同主题 + 新文有实质增量 → 构造 旧+新 合并 markdown → --update
  │   ├─ 完全相同主题 + 新文零增量 → skip
  │   └─ 假阳性（主题不同）→ --force-new
  │
  ├─ 截断 markdown（含 [此处省略XX字]）
  │   └─ ❌ 禁止擅自行动，停下报告给用户 3 选 1（§8.3）
  │
  └─ 空 / None
      └─ ❌ 禁止擅自行动，停下报告给用户 3 选 1（§8.3）
```

### 8.5 update 路径脚本修复后契约（commit `15502d8`）

- `process_inbox.py:1099 fetch_flomo_memo(target_id, keyword=knowledge)`，`knowledge = args.title`
- update 流程能拉到旧 markdown content 用于 AI 决策
- 但脚本没法判断内容是否截断——AI 必须自己看 fetcher 返回字符串判断

### 8.6 复盘段禁令

- 反思产物**写到 MEMORY/notes**——assistant text 输出越短越好
- 禁止在 assistant 输出里写"我错了 / 我反思了 / 下次不再犯 / 请原谅"段
- 公开的复盘段会被看作"找借口"——必须最小回执（"." / "等下一条命令"）

---
