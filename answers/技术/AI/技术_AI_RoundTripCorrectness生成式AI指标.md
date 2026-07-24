#分析框架 #技术 #AI

**技术_AI_RoundTripCorrectness生成式AI指标**

**来源**：网络

**概念**：<mark>Round-Trip Correctness（RTC）</mark>是一种评估生成式AI模型的新指标——不依赖人工标注的基准数据，而是让AI在文本和模型之间"往返转换"（round-trip），通过比较原始与重构结果的相似度来评估生成质量。由SAP Signavio与慕尼黑工业大学联合研究团队提出，应用于业务流程模型（BPMN）的AI生成评估。

**子概念**：
- <mark>背景问题</mark>：LLM自动生成业务流程模型需要评估，但传统方法依赖人工标注ground truth数据——这类数据集稀缺且可能被LLM训练数据污染；RTC提供无需人工参考的替代方案
- <mark>核心方法</mark>：Model→Text→Model（M2M）：从模型生成文本再重构回模型，对比相似度；Text→Model→Text（T2T）：从文本生成模型再转回文本，对比语义保留度
- <mark>定义</mark>：对于"好"的前向M和后向M^-1模型，期望x̂=M^-1(M(x))与x语义等价；实际计算通过少量前向后向样本的平均相似度估算
- <mark>验证结果</mark>：Allamanis等人（2024）在代码合成基准（HumanEval/ARCADE）上证明，RTC评分与传统pass@1指标Pearson相关系数达0.96
- <mark>局限性</mark>：RTC评估的是M和M^-1的组合性能——若前向模型已很差，无法衡量后向模型的独立能力
- <mark>团队与开放资源</mark>：Nataliia Klievtsova等（TUM+SAP Signavio）；代码、结果、数据集均在GitHub公开
