#知识载体 #技术 #AI

**技术_AI_KolegaCode多Agent编码工具**

**来源**：网络

**概念**：<mark>Kolega Code</mark> 是一个本地优先的终端多Agent编码工具——核心功能 <mark>Gigacode</mark> 可扇出多个专业化子Agent并行处理任务（代码审查/迁移/跨文件检查/对抗评审/实施计划等），支持 Plan（只读）+ Build（可写）双模式。Python 3.11+，Apache 2.0 许可证。

**子概念**：
- <mark>Gigacode 编排</mark>：并行/流水线/循环/评审/合成多Agent工作流，可保存工件和恢复中断的运行；工作流阶段头在对话记录中可见，子Agent检查器展示实时轨迹
- <mark>Agent角色</mark>：planning（规划）/ building（编码）/ investigation（调查）/ general（通用）/ browser（浏览器）——不同角色可分配不同模型
- <mark>模型路由</mark>：支持 Anthropic/OpenAI/Google/Groq/DeepSeek/DashScope 等 15+ 提供商；可为长上下文/快速/思考等角色分配不同模型
- <mark>工具集</mark>：仓库读写/精确编辑/终端执行/Web搜索（DuckDuckGo默认免key）/浏览器Playwright/MCP服务器（streamable_http/sse/stdio）
- <mark>安装与使用</mark>：curl安装或 pip/uv 安装；TUI交互模式 + kolega-code ask 单次执行模式；支持 ChatGPT 订阅登录代替API Key
- <mark>本地优先</mark>：会话/设置/权限/OAuth令牌/API密钥均存储在本地机器
