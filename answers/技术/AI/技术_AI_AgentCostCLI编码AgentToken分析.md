#知识载体 #技术 #AI

**技术_AI_AgentCostCLI编码AgentToken分析**

**来源**：网络

**概念**：<mark>AgentCost</mark> 是一个本地开源 CLI 工具——分析 AI 编码代理（Claude Code/Cursor/Codex/Ollama 等）会话中的<mark>Token 消耗归因</mark>，不只统计总额，还回答"哪些因素驱动了 Token 支出"。支持创始人视角（预估 $ 账单）和开发者视角（Token 分项/异常检测/缓存命中）。无需云端、无需账号、无仪表盘。

**子概念**：
- <mark>支持的 AI 工具</mark>：Claude Code（读取 ~/.claude/projects/*.jsonl）、Cursor Agent（~/.cursor/projects/*.jsonl）、OpenAI Codex CLI（~/.codex/sessions/*.jsonl）、Ollama 本地模型（通过 agentcost smoke 或 ollama-proxy）
- <mark>创始人报告</mark>：预估 $$、主要驱动占比（%）、HOT/WARN 异常模式（工具循环/上下文重发）、最热工具/文件；健康信号：initial_prompt 占合理份额，工具循环短；不健康：tool_followup 或单文件占 60%+
- <mark>开发者报告</mark>：in/out token 分 session/turn、cache_read/write（Claude 提示缓存）、call_type（initial_prompt/user_turn/tool_followup/text_response）、TOP CALLS 查看最重 turn + 工具目标
- <mark>数据质量标记</mark>：Cursor 和 Claude 流日志可能省略实际用量→AgentCost 从文本长度估算→打印 DATA QUALITY 块明确标记 estimated/placeholder/directional，非真实发票数据
- <mark>定价表</mark>：本地硬编码（截至 2026-07-24，45 天过期提醒），明确标注 stale 日期和更新步骤；Ollama 本地模型始终 $0
- <mark>技术特点</mark>：Python 3.10+，pip 安装（PyPI 名 agentcost-cli），MIT 许可证；live-tail（watch）、--json 机器可读输出、Ollama 代理模式（ollama-proxy）
