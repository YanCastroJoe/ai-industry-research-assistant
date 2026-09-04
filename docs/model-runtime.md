# DocFlow 模型运行与验收

## 目标

DocFlow 可以使用 OpenAI-compatible 模型完成受约束规划与内容理解，并在接口不可用、超时或输出非法时降级到本地规则。配置模型不等于调用成功，只有单次 Run 中的调用记录可以证明真实模型路径已完成。

## 安全配置

复制 `.env.example` 为 `.env`，仅在本机或服务器填写：

```dotenv
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_API_KEY=<only-in-local-or-server-env>
MODEL_NAME=deepseek-chat
MODEL_INPUT_COST_PER_MILLION=
MODEL_OUTPUT_COST_PER_MILLION=
MODEL_COST_CURRENCY=CNY
```

费率留空时仍记录真实 Token，但成本显示“未配置费率”。不要把 API Key 写入 README、截图、日志、测试夹具或 GitHub Secrets 以外的仓库文件。

## 单次运行证据

`result.execution.model_calls` 分别记录 `planner` 和 `content`：

- `status`：`succeeded`、`failed` 或 `invalid_output`；
- `provider` 与 `model`；
- 提供方 `request_id`；
- `latency_ms`；
- `prompt_tokens`、`completion_tokens` 与 `total_tokens`；
- 可选 `estimated_cost` 与币种；
- 失败时只记录错误类型，不记录密钥或完整响应正文。

`model_path_complete=true` 只在 Planner 与内容理解均使用模型成功时成立。任一阶段失败时，`degraded=true`，最终结果必须明确标为 `rules_fallback`，不能称为模型输出。

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
