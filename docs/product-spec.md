# 产品规格（MVP）

## 1. 用户任务

用户粘贴一段公司、产业或宏观材料，或上传一份 PDF。系统识别材料类型后，生成带原文证据的研究卡片。用户可以审核、编辑或驳回草稿；只有审核通过的内容可导出为研究简报。

## 2. 状态流

```text
draft -> processing -> evidence_ready -> pending_review
pending_review -> approved -> exported
pending_review -> rejected -> processing
```

`processing` 阶段出现解析或模型调用失败时，任务进入 `failed`，保留错误信息和可重试入口。

## 3. 研究卡片数据结构

```json
{
  "material_type": "company | industry | macro",
  "summary": "不超过 120 字的事实摘要",
  "facts": [
    {
      "claim": "可被材料直接支持的事实",
      "evidence": "对应原文片段",
      "source_location": "页码或段落位置"
    }
  ],
  "impact_dimensions": ["经营", "供需", "政策", "成本"],
  "impact_chain": ["事件", "受影响环节", "待确认对象"],
  "industry_analysis": {
    "industry_judgment": "基于事实的产业判断",
    "causal_chain": ["事实 -> 变量 -> 行业传导"],
    "direction_analysis": ["产业环节与验证条件，不映射个股"],
    "risk_reversals": ["可能推翻当前判断的条件"]
  },
  "verification_items": ["需要进一步查证的内容"],
  "risk_notice": "不构成投资建议"
}
```

## 4. 页面与验收

| 页面 | MVP 功能 | 验收条件 |
| --- | --- | --- |
| 新建研究 | 粘贴文本、上传 PDF、选择或自动识别材料类型 | 成功创建任务，显示处理状态 |
| 研究详情 | 展示摘要、事实卡片、证据片段和待验证项 | 每条事实均有非空证据 |
| 审核页 | 通过、编辑、驳回 | 未通过审核不能导出 |
| 历史页 | 查看任务状态与最终简报 | 可按材料类型和状态筛选 |
| 导出 | 生成 Markdown 简报 | 简报含证据和风险提示 |

## 5. 非目标

- 不接入自动交易、不预测价格、不生成买卖建议。
- MVP 不依赖实时爬虫或付费金融数据接口。
- 不将模型推测伪装成已确认事实；材料未覆盖时必须写入待验证项。

## 6. 推荐技术方案

- 前端：React + TypeScript。
- 后端：FastAPI + Python。
- 工作流：显式状态机；审核环节可中断并恢复。
- 存储：SQLite，保存任务、卡片、审核记录与导出结果。
- 模型接入：OpenAI-compatible API，通过环境变量配置，仓库不提交密钥。

先实现文本输入闭环，再接入 PDF 解析；这样能优先验证 Agent 工作流，而不是被文件解析细节阻塞。
