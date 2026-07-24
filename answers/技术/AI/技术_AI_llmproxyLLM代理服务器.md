#知识载体 #技术 #AI

**技术_AI_llmproxyLLM代理服务器**

**来源**：网络

**概念**：<mark>llmproxy</mark> 是一个轻量级高性能 LLM 代理服务器——模拟 Ollama、OpenAI 兼容 API 和 llama.cpp 三种本地运行时 API，将所有请求<mark>透明转发到 NVIDIA 云端 API</mark>。任何原本只支持本地 LLM 的客户端工具无需修改代码即可使用 NVIDIA 云端模型。MIT 许可证。

**子概念**：
- <mark>多协议模拟</mark>：同时模拟 Ollama API、OpenAI /v1 API 和 llama.cpp HTTP API，客户端只需将目标地址指向 llmproxy 即可
- <mark>核心功能</mark>：支持 Chat/Completions/Embeddings、流式响应、多模型发现（暴露 NVIDIA API 可用模型列表）、自动重试
- <mark>技术栈</mark>：Python Flask + Gunicorn 生产部署；Docker Compose 一键部署；入站可选用认证
- <mark>运维特性</mark>：请求/响应日志、遥测、可配置时区时钟；/stats 端点查看实时指标和进程状态
- <mark>文档完善</mark>：9份文档涵盖概述/安装/配置/日志/API参考/使用示例/测试/部署/排错
- <mark>用途</mark>：连接 Open WebUI 等客户端与 NVIDIA 模型服务，零客户端修改即可从本地模型无缝切换到云端
