# AShareAgent

轻量 A 股「多智能体风格」分析台：

- **数据**：本机 AkShare 抓取（免费）
- **AI**：只用 Cursor Agent（就是对话里的我）
- **不需要**：MongoDB / Redis / Vue / 硅基流动 / DeepSeek Key

## 和 TradingAgents-CN 的区别

| | TradingAgents-CN | AShareAgent |
|--|--|--|
| 架构 | FastAPI + Vue + Mongo + Redis | 一个小 Python 包 |
| AI | 外部 LLM API | Cursor |
| 启动 | 一堆服务 | 抓数一条命令 |
| 目标 | 完整平台 | 够用、能出报告 |

## 用法

1. 用 Cursor **打开文件夹** `E:\AShareAgent`（不要用桌面那个空/坏的 TradingAgents 目录）。
2. 抓数：

```bat
E:\AShareAgent\analyze.bat 002128
```

或：

```powershell
cd E:\AShareAgent
E:\TradingAgents-CN\src\venv\Scripts\python.exe -m ashare_agent prepare 002128
```

3. 对 Agent 说：`分析 002128`  
   Agent 会读 `data/002128_context.md`，按 6 个角色写报告，保存到 `reports/`。

已有示例报告：`reports/002128_latest.md`。

## CLI

```text
python -m ashare_agent prepare 002128
python -m ashare_agent show 002128
python -m ashare_agent roles
python -m ashare_agent path 002128
```

## 目录

- `data/` 抓取的 JSON + 上下文
- `reports/` 分析报告
- `ashare_agent/` 源码
- `.cursor/rules/` 强制「只用 Cursor 作 AI」的工作流
