# DocFlow 模型运行与验收

## 目标

DocFlow 可以使用 OpenAI-compatible 模型完成受约束规划与内容理解，并在接口不可用、超时或输出非法时降级到本地规则。配置模型不等于调用成功，只有单次 Run 中的调用记录可以证明真实模型路径已完成。

## 安全配置

复制 `.env.example` 为 `.env`，仅在本机或服务器填写：

```dotenv
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_API_KEY=<only-in-local-or-server-env>
MODEL_NAME=deepseek-v4-flash
MODEL_INPUT_COST_PER_MILLION=
MODEL_INPUT_CACHE_HIT_COST_PER_MILLION=
MODEL_INPUT_CACHE_MISS_COST_PER_MILLION=
MODEL_OUTPUT_COST_PER_MILLION=
MODEL_COST_CURRENCY=CNY
MODEL_COST_RATE_LABEL=
```

费率留空时仍记录真实 Token，但成本显示“未配置费率”。通用 OpenAI-compatible 提供方可填写统一输入费率；当提供方返回 `prompt_cache_hit_tokens` 与 `prompt_cache_miss_tokens` 时，可以改用两档缓存费率。只有输入和输出费率完整时才计算成本，`MODEL_COST_RATE_LABEL` 用来标注费率版本或“峰值上限”等口径。不要把 API Key 写入 README、截图、日志、测试夹具或 GitHub Secrets 以外的仓库文件。

## 单次运行证据

`result.execution.model_calls` 分别记录 `planner` 和 `content`：

- `status`：`succeeded`、`failed` 或 `invalid_output`；
- `provider` 与 `model`；
- 提供方 `request_id`；
- `latency_ms`；
- `prompt_tokens`、`completion_tokens` 与 `total_tokens`；
- 提供方可用时记录 `prompt_cache_hit_tokens` 与 `prompt_cache_miss_tokens`；
- 可选 `estimated_cost` 与币种；
- 成本计算依据 `flat_input` 或 `provider_cache_split` 以及可选费率标签；
- 失败时只记录错误类型，不记录密钥或完整响应正文。

`model_path_complete=true` 只在 Planner 与内容理解均使用模型成功时成立。任一阶段失败时，`degraded=true`，最终结果必须明确标为 `rules_fallback`，不能称为模型输出。

DocFlow 的 Planner 与内容理解都是受证据约束的结构化 JSON 任务。DeepSeek V4 默认启用高强度思考，但这两个阶段显式使用非思考模式并限制最大输出，以降低无必要的延迟和 Token 消耗；该设置不改变本地 SafePlanner、Evidence 或 Verifier 门禁。

## 验收

```powershell
.\check-demo.ps1 -RequireModel
```

验收同时要求：

1. Planner 与内容理解均完成真实模型调用；
2. 两次调用均有请求 ID；
3. 提供方返回真实 Token 用量；
4. Evidence 与字段校验通过；
5. 人工审核前不可导出，审核后可以导出。

普通 `check-demo.ps1` 允许本地规则运行，只用于验证可用性和降级路径，不能作为真实模型效果证据。

Linux 或云服务器使用可移植验收脚本。Basic Auth 密码只从环境变量读取，脚本不提供 `--password` 参数，以免出现在 shell 历史和进程列表中：

```bash
export DOCFLOW_BASE_URL="https://docflow.example.com"
export DOCFLOW_DEMO_USERNAME="interviewer"
read -s -p "DocFlow password: " DOCFLOW_DEMO_PASSWORD; echo
export DOCFLOW_DEMO_PASSWORD
python3 scripts/check_model_runtime.py
```

该脚本会新建隔离 Session，验证健康状态、Planner/内容两次真实模型调用、request ID、Token、Verifier、人工审核和审核后导出。任何模型降级都会以非零状态退出。

## M3 诊断口径

`GET /api/docflow/evaluations/summary` 只汇总当前访客 Session 的 SQLite 历史，返回：

- 终态任务执行成功率、失败率、降级率与重试率；
- 总耗时和模型耗时的 P50/P95；
- 模型调用成功率、输入/输出/缓存 Token 与计价覆盖率；
- Planner、内容理解两个阶段各自的调用数、成功率、P50/P95、Token 和已计价成本；
- 模型、规则与规则降级路径的分组诊断。

计价覆盖率用于区分“成本为零”和“没有完整费率”。费率可能随提供方、时段和缓存策略变化，因此仓库不硬编码价格；部署者应依据官方价格页配置并在标签中写明口径。诊断阈值只服务于固定演示环境，不是生产 SLA。
