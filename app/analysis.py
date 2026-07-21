from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Literal

MaterialType = Literal["company", "industry", "macro"]


@dataclass
class Fact:
    claim: str
    evidence: str
    source_location: str


@dataclass
class ResearchCard:
    material_type: MaterialType
    summary: str
    facts: list[Fact]
    impact_dimensions: list[str]
    impact_chain: list[str]
    verification_items: list[str]
    industry_analysis: dict
    risk_notice: str = "本内容仅用于信息研究与材料整理，不构成投资建议。"
    analysis_mode: str = "demo"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Segment:
    text: str
    page: int | None


TYPE_KEYWORDS: dict[MaterialType, tuple[str, ...]] = {
    "company": ("公司", "公告", "董事会", "营收", "净利润", "股份", "股东", "年度报告", "季度报告"),
    "industry": ("产业", "行业", "供应链", "需求", "产能", "渗透率", "上下游", "技术路线", "市场规模"),
    "macro": ("央行", "利率", "汇率", "GDP", "CPI", "PPI", "社融", "货币政策", "监管"),
}

NOISE_PATTERNS = (
    "免责声明", "请仔细阅读", "分析师披露", "商业关系披露", "本研究报告由",
    "资料来源：", "资料来源", "版权所有", "联系电话", "电子邮箱", "市场预期区间",
)
EXCLUDED_RECOMMENDATION_PATTERNS = ("目标价", "潜在升幅", "买入评级", "卖出评级", "估值")
FINANCIAL_KEYWORDS = {
    "毛利率": 8, "营业收入": 7, "收入": 6, "净利润": 6, "营业利润": 5,
    "同比": 4, "环比": 4, "指引": 5, "产能": 4, "产能利用率": 4,
    "晶圆价格": 5, "价格上行": 4, "资本开支": 3, "订单": 3, "需求": 3,
    "亏损": 3, "现金流": 3, "风险": 2,
}
PROHIBITED_OUTPUT_PATTERNS = ("目标价", "潜在升幅", "买入评级", "卖出评级", "买入", "卖出", "股价", "估值")


def route_material(text: str, requested_type: str = "auto") -> MaterialType:
    if requested_type in TYPE_KEYWORDS:
        return requested_type  # type: ignore[return-value]
    scores = {
        material_type: sum(text.count(keyword) for keyword in keywords)
        for material_type, keywords in TYPE_KEYWORDS.items()
    }
    return max(scores, key=scores.get)  # type: ignore[arg-type, return-value]


def _segments(text: str) -> list[Segment]:
    """Rejoin PDF visual lines into evidence-sized paragraphs and preserve page markers."""
    current_page: int | None = None
    buffer: list[str] = []
    output: list[Segment] = []

    def flush() -> None:
        candidate = re.sub(r"\s+", " ", "".join(buffer)).strip()
        if candidate:
            for sentence in re.split(r"(?<=[。！？；;])", candidate):
                if sentence.strip():
                    output.append(Segment(sentence.strip(), current_page))
        buffer.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        page_match = re.fullmatch(r"\[第\s*(\d+)\s*页\]", line)
        if page_match:
            flush()
            current_page = int(page_match.group(1))
            continue
        if not line:
            flush()
            continue
        if line.startswith(("•", "- ", "●")) and buffer:
            flush()
        buffer.append(line)
        if re.search(r"[。！？；;]$", line):
            flush()
    flush()
    return output


def _clip(value: str, length: int = 120) -> str:
    return value if len(value) <= length else f"{value[:length]}…"


def _is_noise(segment: Segment) -> bool:
    return len(segment.text) < 18 or any(pattern in segment.text for pattern in NOISE_PATTERNS)


def _candidate_score(segment: Segment, material_type: MaterialType) -> int:
    text = segment.text
    if _is_noise(segment) or any(pattern in text for pattern in EXCLUDED_RECOMMENDATION_PATTERNS):
        return -100
    if text.startswith((
        "图表", "表 ", "美元百万", "人民币百万", "季度指引", "利润率",
        "财务报表", "盈利能力", "营运能力", "资产负债表", "利润表", "现金流量表",
    )):
        return -100
    if len(text) > 420:
        return -100
    score = sum(weight for keyword, weight in FINANCIAL_KEYWORDS.items() if keyword in text)
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|亿|万|个百分[点比]|美元|人民币|港元)", text):
        score += 3
    if text.startswith(("•", "- ", "●")):
        score += 3
    if material_type == "company" and any(keyword in text for keyword in ("公司", "业绩", "季度", "财务")):
        score += 2
    if material_type == "industry" and any(keyword in text for keyword in ("产业", "行业", "供需", "上下游")):
        score += 2
    if material_type == "macro" and any(keyword in text for keyword in ("政策", "利率", "数据", "监管")):
        score += 2
    return score


def _source_location(segment: Segment, index: int) -> str:
    return f"PDF 第 {segment.page} 页" if segment.page else f"文本第 {index + 1} 段"


def _remove_prohibited_text(value: str) -> str:
    chunks = re.split(r"(?<=[。；;])", value)
    return "".join(chunk for chunk in chunks if not any(pattern in chunk for pattern in PROHIBITED_OUTPUT_PATTERNS)).strip()


def _clean_items(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    return [cleaned for item in items if isinstance(item, str) and (cleaned := _remove_prohibited_text(item))]


def _local_industry_analysis(material_type: MaterialType, facts: list[Fact]) -> dict:
    reference = facts[0].claim if facts else "材料未提供足够的业务事实"
    focus = {
        "company": "经营与财务变量向行业供需的传导",
        "industry": "产业供需与上下游环节的传导",
        "macro": "宏观变量向行业需求环境的传导",
    }[material_type]
    return {
        "industry_judgment": f"演示推演：应围绕{focus}展开；当前最强依据是“{_clip(reference, 80)}”。",
        "causal_chain": ["事实变化 -> 受影响的经营或供需变量 -> 需要后续数据验证的行业影响"],
        "direction_analysis": ["只输出产业环节和验证条件，不直接映射具体股票或给出交易建议。"],
        "risk_reversals": ["单一材料不足以确认行业趋势，需要结合后续数据与多来源材料核验。"],
    }


def _normalize_industry_analysis(value: object, facts: list[Fact]) -> dict:
    raw = value if isinstance(value, dict) else {}
    fallback = _local_industry_analysis("industry", facts)
    judgment = _remove_prohibited_text(str(raw.get("industry_judgment", "")))
    return {
        "industry_judgment": judgment or fallback["industry_judgment"],
        "causal_chain": _clean_items(raw.get("causal_chain")) or fallback["causal_chain"],
        "direction_analysis": _clean_items(raw.get("direction_analysis")) or fallback["direction_analysis"],
        "risk_reversals": _clean_items(raw.get("risk_reversals")) or fallback["risk_reversals"],
    }


def _local_card(text: str, requested_type: str) -> ResearchCard:
    material_type = route_material(text, requested_type)
    segments = _segments(text)
    ranked = sorted(enumerate(segments), key=lambda item: _candidate_score(item[1], material_type), reverse=True)
    selected = [(index, segment) for index, segment in ranked if _candidate_score(segment, material_type) > 0][:4]
    if not selected:
        selected = [(index, segment) for index, segment in enumerate(segments) if not _is_noise(segment)][:3]
    facts = [
        Fact(
            claim=_clip(segment.text.lstrip("•●- "), 150),
            evidence=_clip(segment.text.lstrip("•●- "), 360),
            source_location=_source_location(segment, index),
        )
        for index, segment in selected
    ]
    dimensions = {
        "company": ["经营", "财务", "合规"],
        "industry": ["供需", "竞争格局", "技术路线"],
        "macro": ["政策", "流动性", "需求环境"],
    }[material_type]
    chains = {
        "company": ["公司事件", "经营或财务变量", "需结合后续披露验证的影响"],
        "industry": ["产业事件", "受影响环节", "需核验的公司关系"],
        "macro": ["宏观或政策事件", "行业传导方向", "需跟踪的落地数据"],
    }[material_type]
    verification = [
        "材料仅反映单一来源，需结合原始公告或权威数据交叉核验。",
        "影响链路为研究框架，不能替代对具体公司关系和数据口径的核查。",
    ]
    summary = _clip("；".join(fact.claim for fact in facts[:2]), 180)
    return ResearchCard(
        material_type=material_type,
        summary=summary or "未识别到可分析的有效文本。",
        facts=facts,
        impact_dimensions=dimensions,
        impact_chain=chains,
        verification_items=verification,
        industry_analysis=_local_industry_analysis(material_type, facts),
    )


def _extract_json(content: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        raise ValueError("模型未返回 JSON 对象")
    return json.loads(match.group(0))


def _model_card(text: str, requested_type: str) -> ResearchCard:
    base_url = os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    api_key = os.getenv("MODEL_API_KEY", "")
    model = os.getenv("MODEL_NAME", "gpt-4o-mini")
    system_prompt = """你是证据化产业研究 Agent，不是研报摘要器。只能依据用户给出的材料工作，不得补充材料外事实。
返回严格 JSON，字段为：material_type(company|industry|macro)、summary、facts、impact_dimensions、impact_chain、verification_items、industry_analysis、risk_notice。
facts 是数组，每项含 claim、evidence、source_location；claim 是事实的简洁转述，evidence 必须是材料原文片段。
industry_analysis 是对象，包含：industry_judgment、causal_chain、direction_analysis、risk_reversals，后 3 项均为字符串数组。
产业推演要求：
1. 先从事实推导供需、价格、产能、成本、技术路线或需求传导；每条推演必须显式写“依据：事实 X”或“基于材料推演”。
2. direction_analysis 只能写产业环节、受影响变量和验证条件，不得映射个股。
3. 不得输出、复述或评价目标价、评级、估值、股价、买入、卖出等内容，即使原材料包含它们。
4. 不确定内容必须放入 verification_items 或 risk_reversals，不得伪装成事实。
5. summary 必须是研究结论，不得逐句照抄原文。"""
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"材料类型偏好：{requested_type}\n材料：\n{text}"},
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        result_payload = _extract_json(result["choices"][0]["message"]["content"])
        facts = [
            Fact(**fact)
            for fact in result_payload.get("facts", [])
            if isinstance(fact, dict) and not any(pattern in str(fact) for pattern in PROHIBITED_OUTPUT_PATTERNS)
        ]
        if not facts:
            return _local_card(text, requested_type)
        return ResearchCard(
            material_type=result_payload.get("material_type", route_material(text, requested_type)),
            summary=_remove_prohibited_text(result_payload.get("summary", "")) or facts[0].claim,
            facts=facts,
            impact_dimensions=_clean_items(result_payload.get("impact_dimensions")),
            impact_chain=_clean_items(result_payload.get("impact_chain")),
            verification_items=_clean_items(result_payload.get("verification_items")),
            industry_analysis=_normalize_industry_analysis(result_payload.get("industry_analysis"), facts),
            risk_notice=result_payload.get("risk_notice", "本内容仅用于信息研究与材料整理，不构成投资建议。"),
            analysis_mode="model",
        )
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, json.JSONDecodeError):
        return _local_card(text, requested_type)


def analyze(text: str, requested_type: str = "auto") -> ResearchCard:
    if not text.strip():
        raise ValueError("请粘贴需要分析的材料。")
    if os.getenv("MODEL_API_KEY"):
        return _model_card(text, requested_type)
    return _local_card(text, requested_type)
