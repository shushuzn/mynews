# AGENT.md — 项目级 Agent 行为硬规则

> 本文件是 `/root/mynews/struct-doc-answer/SKILL.md` 的**前置精简版**——给任何 Agent（包括跨会话/跨项目）一进入项目就能读到的全局硬规则。完整 SKILL 见 `./struct-doc-answer/SKILL.md`。

---

## 1. 核心流程：微信公众号 URL → flomo 笔记

**唯一入口**：`process_inbox.py`（位于 `scripts/process_inbox.py`）。禁止绕开脚本直接调 `upload_flomo` / `memo_create`。

### 标准四步（顺序不可乱）

```
Step 1: fetch_wechat_article(use_cache=False)
        → 标题 / 来源 / 字符数 / 正文预览（≥600 字符）

Step 2: 构造完整长 ai-content
        → 必须有 **概念** 段（含 <mark> 高亮）
        → 必须有 **子概念** 段（≥6 个 <mark> 要点，论点+原文关键数据）

Step 3: 主题级决策（in-context 单独做）
        ├─ 完全相同主题 + 有实质增量 → 下一步传 --update MEMO_ID
        ├─ 完全相同主题 + 无增量 → skip（不重跑脚本）
        ├─ 假阳性（关键词命中但主题不同）→ 下一步传 --force-new

Step 4: process_inbox.py 一次跑完（带 --update 或 --force-new）
        → 脚本自动完成：抓取/创建文件/hook 验证/查重/上传
```

总流程固定为 **4 步**：relevance ≥ 0.9 与 relevance < 0.9 都是 4 步完成；Step 3 决策在大脑做，Step 4 真正写。

### 禁止事项
- 跳过 Step 1 拿 in-context 残留 Step 1 输出
- 跑 process_inbox 不传完整 ai-content（短占位符事故：`MjQ4MDE2NjY8` 钛酸镍测试）
- 同 memo_id 在一 session 内写入 > 1 次
- 跑两次 process_inbox.py 拿 old_id（探测式 + 真实写）—— 探针会留 .md 残留
- relevance ≥ 0.9 时手动 fetch_flomo_memo（旧文档脚本已自动打到 stderr）

### 错误案例（禁止类比）

**案例 1**：短 ai-content 探针跑一次 + 长 ai-content 真实再跑一次 = 流程里多了 1 步违规探针。
**案例 2**：relevance ≥ 0.9 时 fetch_flomo_memo 取全文（脚本已打在 stderr）= 多 1 步冗余。
**案例 3**：先 search_flomo 拿 old_id（脚本已 search）= 多 1 步冗余。

---

## 2. 命令词强制语义

| 用户说 | AI 必须做 |
|---|---|
| **"重做"** / "再跑一次" / "重新做" / "重新跑" | **整套重跑 Step 1 + Step 2 + Step 3 + Step 4** |
| **"重写 ai-content"** / "重新构造 ai-content" | 仅重跑 Step 2 + Step 3 + Step 4 |
| **"重新 update"** / "重新合并" | 仅重跑 Step 3 + Step 4（仍受同 memo_id ≤1 限制） |
| **"重跑 Step 4"** / "再写一次" | 仅重跑 Step 4（同 memo_id 仍受 ≤1 限制） |

**铁规**：用户说"重做"未指定哪步 → 默认整套重跑 Step 1+2+3+4。

---

## 3. 查重与 relevance 决策

### 脚本默认行为
- **relevance < 0.9**：脚本自动 continue 新建 — AI 不主动插手
- **relevance ≥ 0.9**：脚本自动 `fetch_flomo_memo` 拉旧文档到 stderr + 打决策表

### AI 在 relevance ≥ 0.9 时必须看 stderr
```
========== 已有笔记内容（id={OLD}, N 字符） ==========
<旧 markdown 完整全文>
========== 新文章内容（N 字符） ==========
<新 body_text 全文>
```

### 决策矩阵
| 主题 | 增量 | 操作 |
|---|---|---|
| 完全相同主题 | 有实质增量（数据/事件/时间/参数） | `--update OLD_ID`（合并 markdown）|
| 完全相同主题 | 零增量 | skip（不重跑脚本）|
| 假阳性（关键词命中但主题不同）| — | `--force-new` |

---

## 4. update_flomo = 覆盖操作

`update_flomo` 通过 `memo_update` API 把传入的 content **整体覆盖**到指定 memo_id。"合并"是 AI 的责任——必须先按主题级比对，再构造"旧+新"单一 markdown 一次性传 `--ai-content`。

### fetcher 截断或拿不到完整旧文时
- ❌ 禁止擅自 `--force-new`（=伪造"新主题"决定）
- ❌ 禁止擅自 `--update`（=擅自当覆盖决定，旧子概念全丢）
- ❌ 禁止用 in-context 部分残留 + 上一轮 upload 拼"旧版本"
- ✅ 停下报告 + 给用户 3 选 1（skip / --force-new / 用户提供完整旧 ai-content）

---

## 5. AI 红线（行为硬约束）

| 红线 | 含义 |
|---|---|
| 不擅自 commit / push / rm | 等用户显式批准；commit 后立即推双 remote |
| 不擅自 clear cache / use_cache=False 验证 | 等用户批准 |
| 不用 memory/history 召回本轮 ai-content | MEMORY 红线——禁止以"参考上次"为由 |
| 完成即停手 | 工具调用完不追加复盘段/承诺/解释段 |
| 被骂时最小回执是 "." 或 "等下一条命令" | 禁止"我错了/下次不会" |
| 反思产物写 MEMORY | 不在 assistant 输出里写复盘段 |
| `--ai-content` 避免内嵌英文 `""` `''` | 改用中文 `""` 或中文括号 |
| 文学虚构类 URL 也走完整流程 | 用户命令=合法 |

---

## 6. 文件名 6 类禁用字符（pre-commit hook 必校验）

```
FILENAME_PATTERN = re.compile('^[a-zA-Z0-9_\-\.\u4e00-\u9fff()%]+$')
```

| 禁用字符 | 替代方案 |
|---|---|
| 半角空格 | `-` |
| 半角斜杠 `/` | `拼` 或合并 |
| 半角加号 `+` | `加` |
| 全角冒号 `：` | `-` |
| en-dash `–` (U+2013) | `-` |
| 连续 `_`+字母数字混合（如 RISC_V/M_O_Si/F_m）| 合并或加字母 |

专有技术词（含 `-` 或 `_`）一律合并，不依赖 `_` 分隔。

---

## 7. 标签规则

**第一项必须是信号类型标签**（5 选 1）：

| 标签 | 内容特征 | 适用场景 |
|---|---|---|
| `#知识基座` | 概念/定理/历史/机制 | 客观知识、定律、历史事件、技术原理 |
| `#趋势信号` | 正在发生的结构性变化 | 行业动态、政策转向、市场趋势 |
| `#信号笔记` | 单次事件/数据点 | 具体新闻事件、数据点、案例 |
| `#分析框架` | 可复用的思维模型/方法论 | 思维工具、认知模型 |
| `#知识载体` | 工具/资源/数据集 | 工具介绍、资源列表、书单 |

标签 ≥3 个；领域标签如 `#技术` `#AI` 跟在信号类型后。

---

## 8. 领域一级名（不要混用）

合法一级 `--domain`：技术 / 经济 / 政治 / 自然科学 / 社会科学 / 医学（"管理"也是一级，但**不要用 `--subdomain 管理` + `--tags #管理` 同时用**——触发一级领域冲突）。

---

## 9. 反思 vs 复盘段

- **复盘段禁令**：禁止 assistant 输出里写"我错了 / 我反思了 / 请原谅"。
- **反思产物**：写到 `/root/.local/share/mimocode/memory/projects/global/MEMORY.md` 的 `## Discovered durable knowledge` 下，或 `sessions/{sid}/notes.md`。
- **被骂时的最小回执**：`.` 或 `等下一条命令`。

---

## 10. 参考

完整 SKILL：`./struct-doc-answer/SKILL.md`
全局 MEMORY：`/root/.local/share/mimocode/memory/projects/global/MEMORY.md`
脚本：`./scripts/process_inbox.py`
