"""终裁结构化输出（对齐 TradingAgents-CN signal_processing）。

不调用外部 API；仅定义 schema / 校验，供 Cursor 报告末尾输出。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


# 原项目仅允许：买入 / 持有 / 卖出
ALLOWED_ACTIONS = ("买入", "持有", "卖出")

DECISION_JSON_SCHEMA = {
    "action": "买入|持有|卖出",
    "target_price": "number  # 基准情景目标价（人民币）",
    "target_price_conservative": "number",
    "target_price_optimistic": "number",
    "price_range": {"low": "number", "high": "number"},
    "stop_loss": "number|null",
    "confidence": "number  # 0-1",
    "risk_score": "number  # 0-1，越高风险越大",
    "time_horizon": "1-3个月",
    "reasoning": "string  # 一两句依据，勿编造数据",
}

DECISION_RULES = """
终裁结构化输出（对齐 TradingAgents-CN signal_processing）：
1. 第 8 节末尾必须追加一个 JSON 代码块，字段按 DECISION_JSON_SCHEMA。
2. action 只能是「买入」「持有」「卖出」三选一（不要用英文 buy/hold/sell）。
   - 若研究结论是观望/跟踪，action 用「持有」，并在 reasoning 写明观望原因。
3. target_price 为基准情景具体数值；同时给 conservative / optimistic。
4. confidence、risk_score 为 0–1 数值；禁止 null。
5. 该 JSON 仅供研究结构化存档，不构成投资建议。
""".strip()


def validate_decision(obj: Dict[str, Any]) -> Dict[str, Any]:
    """尽力规范化终裁字典；缺字段时填默认并标记 warnings。"""
    warnings = []
    action = str(obj.get("action") or "持有").strip()
    mapping = {
        "buy": "买入",
        "BUY": "买入",
        "sell": "卖出",
        "SELL": "卖出",
        "hold": "持有",
        "HOLD": "持有",
        "观望": "持有",
        "跟踪": "持有",
    }
    action = mapping.get(action, action)
    if action not in ALLOWED_ACTIONS:
        warnings.append(f"非法 action={action}，回退为持有")
        action = "持有"

    def _f(key: str) -> Optional[float]:
        v = obj.get(key)
        try:
            if v is None or v == "" or v == "null":
                return None
            return float(v)
        except Exception:
            return None

    out = {
        "action": action,
        "target_price": _f("target_price"),
        "target_price_conservative": _f("target_price_conservative"),
        "target_price_optimistic": _f("target_price_optimistic"),
        "stop_loss": _f("stop_loss"),
        "confidence": _f("confidence"),
        "risk_score": _f("risk_score"),
        "time_horizon": str(obj.get("time_horizon") or "1-3个月"),
        "reasoning": str(obj.get("reasoning") or ""),
    }
    pr = obj.get("price_range") or {}
    if isinstance(pr, dict):
        out["price_range"] = {"low": _f_from(pr, "low"), "high": _f_from(pr, "high")}
    else:
        out["price_range"] = {"low": None, "high": None}

    for k in ("confidence", "risk_score"):
        if out[k] is None:
            warnings.append(f"缺少 {k}")
        else:
            out[k] = max(0.0, min(1.0, float(out[k])))
    if out["target_price"] is None:
        warnings.append("缺少 target_price")
    if warnings:
        out["warnings"] = warnings
    return out


def _f_from(d: dict, key: str) -> Optional[float]:
    try:
        v = d.get(key)
        if v is None or v == "" or v == "null":
            return None
        return float(v)
    except Exception:
        return None


def extract_decision_from_markdown(text: str) -> Optional[Dict[str, Any]]:
    """从报告 Markdown 中提取最后一个 JSON 决策块。"""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.S)
    for raw in reversed(blocks):
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict) and ("action" in obj or "target_price" in obj):
            return validate_decision(obj)
    return None


def decision_block_example() -> str:
    example = {
        "action": "持有",
        "target_price": 25.2,
        "target_price_conservative": 22.1,
        "target_price_optimistic": 27.0,
        "price_range": {"low": 22.5, "high": 25.5},
        "stop_loss": 22.5,
        "confidence": 0.62,
        "risk_score": 0.68,
        "time_horizon": "1-3个月",
        "reasoning": "空头排列未扭转，基准目标价看向 MA20 修复带；观望为主。",
    }
    return "```json\n" + json.dumps(example, ensure_ascii=False, indent=2) + "\n```"
