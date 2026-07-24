#知识基座 #技术 #软件开发

**技术_软件开发_OpenTelemetryCollector生产指南**

**来源**：网络

**概念**：<mark>OpenTelemetry Collector</mark> 是一个供应商中立的遥测数据管道服务，可接收、处理、导出日志/指标/链路追踪数据。支持原生 OTLP 协议及 Jaeger/Prometheus 等格式。核心架构三组件：<mark>Receivers</mark>（数据入口）、<mark>Processors</mark>（数据转换/批处理/采样）、<mark>Exporters</mark>（发送至后端）。部署推荐 Agent+Gateway 混合模式。

**子概念**：
- <mark>使用 Collector 的理由</mark>：供应商无关（切换后端无需改应用代码）、数据处理与丰富（清洗/PII移除/基数降低）、降低应用开销（批量/重试/压缩由Collector处理）、集中管理遥测路由和配置、自动收集主机指标（CPU/内存/磁盘）
- <mark>部署模式</mark>：Agent模式（与应用同主机，如K8s Sidecar）——丰富元数据/早期过滤但资源消耗高；Gateway模式（集中式Collector集群）——集中管理/减少出口点但需HA；推荐混合模式
- <mark>必备处理器</mark>：memory_limiter（check_interval:1s, limit_mib:500, spike_limit_mib:128）；batch（send_batch_size:8192, timeout:10s）；旧 queued_retry 功能已内置于导出器的 retry_on_failure/sending_queue
- <mark>生产最佳实践</mark>：prometheus导出器暴露自身指标、health_check扩展用于K8s存活性探针、配置sending_queue启用磁盘缓冲和重试、TLS加密通信、始终包含memory_limiter+batch
- <mark>常见错误</mark>：context deadline exceeded（网络问题/超时）；TLS handshake失败（对本地Collector禁用TLS）；401/403（凭据无效）；内存超限（确保memory_limiter在管线最前）；unknown type（需otelcol-contrib发行版）
- <mark>数据持久性</mark>：新版本中 sending_queue 和 retry_on_failure 直接在导出器配置，支持磁盘缓冲，确保数据不因Collector重启丢失
