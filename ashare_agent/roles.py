"""多智能体角色说明（供 Cursor Agent 遵循，不调用外部 LLM API）。

对齐 TradingAgents-CN 核心分析链路，压成 Cursor 可一次写完的报告结构：
分析师层 → 多空辩论 → 风险 → 交易员/综合裁决。
"""

ROLES = [
    {
        "id": "market",
        "name": "市场/技术面分析师",
        "focus": "价格趋势、均线结构、MACD/RSI/布林/ATR/KDJ、量价与短中期结构",
    },
    {
        "id": "china_market",
        "name": "中国市场分析师",
        "focus": "A股政策语境、板块资金、公告事件、本土交易制度与题材风险；缺数据要明说",
    },
    {
        "id": "fundamental",
        "name": "基本面分析师",
        "focus": "财务摘要、估值、盈利质量、杠杆与现金流；缺数据要明说",
    },
    {
        "id": "news",
        "name": "新闻与公告分析师",
        "focus": "近期新闻主题、公司公告事件、利好利空、可信度与滞后性",
    },
    {
        "id": "social",
        "name": "社媒情绪分析师",
        "focus": "雪球关注、东财股吧得分；区分热度与真实基本面",
    },
    {
        "id": "bull",
        "name": "看涨研究员",
        "focus": "基于前述报告提炼多头论据、催化剂与被低估点；禁止编造数据",
    },
    {
        "id": "bear",
        "name": "看跌研究员",
        "focus": "基于前述报告提炼空头论据、证伪条件与估值/趋势风险；禁止编造数据",
    },
    {
        "id": "risk",
        "name": "风险官",
        "focus": "从激进/中性/保守三视角评估下行风险、流动性、主题炒作与失效条件",
    },
    {
        "id": "trader",
        "name": "交易员",
        "focus": "把研究结论落成可执行的研究仓位框架（非投资建议）：情景、触发条件、退出条件",
    },
    {
        "id": "judge",
        "name": "综合裁决",
        "focus": "汇总多空与风险，给出研究结论倾向与关键监控指标（非投资建议）",
    },
]

REPORT_SECTIONS = [
    "1) 市场/技术面分析师",
    "2) 中国市场分析师",
    "3) 基本面分析师",
    "4) 新闻与公告分析师",
    "5) 社媒情绪分析师",
    "6) 多空辩论（看涨研究员 / 看跌研究员）",
    "7) 风险官（激进 / 中性 / 保守视角）",
    "8) 交易员与综合裁决",
]

SYSTEM_RULES = """
你是 AShareAgent 的唯一 AI 后端（运行在 Cursor 中）。
硬约束：
1. 禁止要求用户配置 OpenAI/DeepSeek/硅基流动等外部 Key；禁止调用外部大模型 API。
2. 只能基于 fetch 产出的数据包（data/<代码>_context.md / .json）分析，禁止编造财报/新闻/公告数字。
3. 输出中文，严格按 REPORT_SECTIONS 分段；有数据引用，不确定就写「数据缺失」。
4. 明确这是研究学习用途，不是投资建议；报告末尾必须有免责声明。
5. 对 A 股社媒只使用公开热度/股吧指标，不假装有 Reddit/Twitter。
6. 技术面须引用数据包中的 MA/MACD/RSI/布林/ATR/KDJ（若存在）；资金流/公告缺失时不要脑补。
""".strip()


def report_outline(code: str, name: str = "") -> str:
    title = f"{code} {name}".strip()
    lines = [
        f"# {title} — 多智能体研究报告",
        "",
        "- AI 后端: 仅 Cursor（无外部 LLM API）",
        "- 用途: 研究学习，不构成投资建议",
        "",
        "---",
        "",
    ]
    for sec in REPORT_SECTIONS:
        lines.append(f"## {sec}")
        lines.append("")
        lines.append("（在此填写）")
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.extend(
        [
            "## 免责声明",
            "",
            "本报告仅供研究学习，基于公开数据抓取与多角色结构化讨论生成，"
            "不构成任何投资建议、要约或保证。市场有风险，决策请自行负责并咨询持牌专业人士。",
            "",
        ]
    )
    return "\n".join(lines)
