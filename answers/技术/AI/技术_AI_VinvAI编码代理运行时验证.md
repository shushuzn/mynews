#知识载体 #技术 #AI

**技术_AI_VinvAI编码代理运行时验证**

**来源**：网络

**概念**：<mark>Vinv</mark> 是一个编码代理（Coding Agent）的<mark>运行时真相（Runtime Ground Truth）</mark>系统——当 AI 编码代理声称"完成了"时，Vinv 要求它证明。通过零编辑追踪器记录 Python 服务的真实运行轨迹，构建实时代码图，用代理从未见过的验收测试进行<mark>闭环验证</mark>。

**子概念**：
- <mark>零编辑追踪器（Tracelens）</mark>：无需修改代码/SDK/仪表盘，即可记录 Python 服务的每次调用——时间/内存/参数/返回值/错误和触发请求
- <mark>实时代码图</mark>：基于本地嵌入模型（CodeRankEmbed 137M参数，MIT许可）的持续语义索引和交互式代码图；保存文件时增量更新，4000+符号运行时覆盖层
- <mark>MCP服务</mark>：通过 MCP 协议向编码代理提供上下文——vinv-index（代码库/会话查询）+ vinv-runtime（运行轨迹/故障定位/调用链）
- <mark>闭环验证</mark>：代理声称修复后→Vinv自动重放启动命令→检查端口服务→运行验收测试→失败则循环继续；证据不足时生成人工判断卡
- <mark>与工作流集成</mark>：自动发现仓库中所有服务（HTTP/Dev Server/MCP Server）；完全通过用户已配置的编码代理CLI调用LLM，Vinv自身无需LLM API密钥
- <mark>隐私架构</mark>：完全本地运行——无遥测、无网络请求（首次下载嵌入模型除外）；追踪数据摘要存储（字符串截断/敏感参数自动脱敏）；Apache 2.0许可证
