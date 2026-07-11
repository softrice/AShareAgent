# AShareAgent

轻量 A 股「多智能体风格」分析台：从 [TradingAgents-CN](https://github.com/softrice/TradingAgents-CN) 抽出**核心分析能力**，用本机公开数据 + Cursor 完成报告。

- **数据**：本机 AkShare / 东财公开接口（免费）
- **AI**：只用 Cursor Agent（对话里的我）
- **不需要**：MongoDB / Redis / Vue / 硅基流动 / DeepSeek Key

## 和 TradingAgents-CN 的区别

| | TradingAgents-CN | AShareAgent |
|--|--|--|
| 架构 | FastAPI + Vue + Mongo + Redis | 一个小 Python 包 |
| AI | 外部 LLM API | Cursor |
| 启动 | 一堆服务 | 抓数一条命令 |
| 目标 | 完整平台 | 够用、能出报告 |

## 核心能力（已对齐原项目分析链路）

- 技术面：MA / MACD / RSI / 布林 / ATR / KDJ
- 基本面：财务摘要 + 估值快照
- 新闻 + **公司公告**
- 社媒：雪球关注 / 东财股吧
- 资金流向（可得时）
- 报告角色：分析师 → 多空辩论 → 风险官 → 交易员/综合裁决（**含目标价三情景**）

## 安装

```bash
cd AShareAgent
python -m venv .venv

# Windows
.venv\Scripts\pip install -r requirements.txt

# Linux / macOS
.venv/bin/pip install -r requirements.txt
```

## 用法

1. 用 Cursor **打开本仓库文件夹**。
2. 抓数：

```bat
analyze.bat 002128
```

或：

```bash
python -m ashare_agent prepare 002128
```

3. 对 Agent 说：`分析 002128`  
   Agent 会读 `data/002128_context.md`，按角色写报告，保存到 `reports/`。

## CLI

```text
python -m ashare_agent prepare 002128
python -m ashare_agent show 002128
python -m ashare_agent roles
python -m ashare_agent path 002128
python -m ashare_agent outline 002128
python -m ashare_agent save 002128 --file report.md
```

## 目录

- `data/` 抓取的 JSON + 上下文
- `reports/` 分析报告
- `ashare_agent/` 源码（`fetch` / `indicators` / `roles` / `cli`）
- `.cursor/rules/` 强制「只用 Cursor 作 AI」的工作流

## 免责声明

研究学习用途，不构成投资建议。
