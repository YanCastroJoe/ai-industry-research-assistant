# DocFlow 项目摘要（简历/面试口径）

## 一句话定位

面向企业项目协作场景的可恢复文档 Agent：将材料与自然语言目标拆成受约束的工具执行链，生成带引用的周报、风险清单和汇报大纲，并通过检查点恢复与人工审核降低长链路失败和内容失真风险。

## 简历版项目描述

**DocFlow 协作式文档 Agent｜个人项目**

**技术栈：** Python、FastAPI、SQLite、OpenAI-compatible API、MCP、Vanilla JavaScript

### 一页简历精简版（推荐）

- 设计 `Planner → Tool Registry → Agent Runtime` 工作流，以 Instruction/Source/Memory/Evidence 分层上下文驱动检索、事实抽取和带 `[E#]` 引用的文档生成，并接入人工审核。
- 实现 SafePlanner 工具白名单、RulePlanner 回退、超时重试与 SQLite Trace/Checkpoint；支持异步排队、幂等提交及 `queued` 任务重启恢复。
- 基于官方 MCP Python SDK 接入 stdio 工具，并以 AgentOps 汇总引用和运行诊断；DocFlow 相关 19 项测试通过，固定回归集 20/20（仅代表固定集）。

### 详细版

- 针对项目材料分散、文档生成过程不可追踪的问题，设计 Planner → Tool Registry → Agent Runtime 工作流，将检索、事实抽取、内容生成、引用校验和人工审核拆为 6–7 个可观测步骤，输出带 `[E#]` 证据引用的周报、风险清单与汇报大纲。
- 针对模型规划可能越权或产生非法步骤的问题，实现 LLM Planner 与 RulePlanner 双路径，并以工具白名单、依赖顺序、重复调用和最大步数校验约束执行计划；模型不可用或计划不合法时自动回退。
- 针对长链路工具瞬时故障导致整次任务重跑的问题，实现超时、有限重试、错误分类与 SQLite 检查点恢复，按 Run/Step/Attempt 记录执行轨迹，可从最近成功步骤继续执行。
- 实现 Instruction/Source/Memory/Evidence 分层上下文与可视化 Trace，支持受众、信息焦点和证据预算配置；新增受控异步队列、SQLite 排队任务重启恢复、幂等提交、Request ID/JSON 日志、任务进度轮询及 AgentOps 质量门禁，并基于官方 MCP SDK 完成 stdio 工具发现与调用。
- 重构演示界面为“任务创建 → 执行检查 → 人工审核”的渐进式路径，提供业务模板和独立 AgentOps 诊断页；界面用于呈现真实运行数据，不把静态流程图包装成多 Agent 能力。
- 建立项目测试套件 26 项（其中 DocFlow 相关 19 项），并构建 20 条固定任务与合成故障场景；当前本地规则模式下固定集 20/20 通过，工具选择 Precision/Recall 与引用通过率均为 100%。

## 面试时必须主动说明

- 20/20 是小规模合成回归集结果，不是生产准确率。
- 引用校验主要验证 Evidence ID 可追溯性和部分一致性，不是完整事实蕴含模型。
- MCP 已走真实协议，但当前接的是本地示例 Server，未冒充生产第三方系统集成。
- 异步执行仍是单应用进程内的受控线程池；仅 `queued` 任务可从 SQLite 自动重新入队，执行中的任务会失败并保留重试入口，并非 Redis/Celery 等生产级分布式任务系统。
- 项目是单 Agent 可控工作流，不宣称多 Agent 或通用自主 Agent。
