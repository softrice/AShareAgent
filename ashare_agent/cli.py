"""CLI: 准备数据 / 展示上下文。AI 分析在 Cursor 对话中完成。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .fetch import DATA_DIR, REPORTS_DIR, prepare, normalize_code
from .roles import SYSTEM_RULES, ROLES


def cmd_prepare(code: str) -> int:
    print(f"[AShareAgent] 抓取 {code} ...")
    payload = prepare(code)
    print(f"[OK] JSON: {payload['json_path']}")
    print(f"[OK] 上下文: {payload['context_path']}")
    print()
    print("下一步（二选一）：")
    print("  1) 在 Cursor 对 AShareAgent 项目说：分析 " + normalize_code(code))
    print("  2) 把 context 文件发给 Cursor Agent 继续")
    print()
    print("AI 后端: 仅 Cursor（无外部大模型 Key）")
    return 0


def cmd_show(code: str) -> int:
    code = normalize_code(code)
    path = DATA_DIR / f"{code}_context.md"
    if not path.exists():
        print(f"缺少 {path}，请先: python -m ashare_agent prepare {code}")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_roles(_: str = "") -> int:
    print(SYSTEM_RULES)
    print()
    for r in ROLES:
        print(f"- {r['name']}: {r['focus']}")
    print()
    print(f"数据目录: {DATA_DIR}")
    print(f"报告目录: {REPORTS_DIR}")
    return 0


def cmd_path(code: str) -> int:
    code = normalize_code(code)
    print(f"JSON: {DATA_DIR / (code + '.json')}")
    print(f"上下文: {DATA_DIR / (code + '_context.md')}")
    print(f"最新报告: {REPORTS_DIR / (code + '_latest.md')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ashare_agent",
        description="轻量 A 股分析：本地抓数 + Cursor 作为唯一 AI",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("prepare", help="抓取股票数据包")
    p1.add_argument("code", help="股票代码，如 002128")

    p2 = sub.add_parser("show", help="打印已抓取的上下文")
    p2.add_argument("code", help="股票代码")

    sub.add_parser("roles", help="查看多智能体角色说明")

    p3 = sub.add_parser("path", help="打印数据/报告路径")
    p3.add_argument("code", help="股票代码")

    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args.code)
    if args.cmd == "show":
        return cmd_show(args.code)
    if args.cmd == "roles":
        return cmd_roles()
    if args.cmd == "path":
        return cmd_path(args.code)
    return 2


if __name__ == "__main__":
    sys.exit(main())
