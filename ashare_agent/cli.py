"""CLI: 准备数据 / 展示上下文 / 保存报告。AI 分析在 Cursor 对话中完成。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .fetch import DATA_DIR, REPORTS_DIR, prepare, normalize_code, save_report
from .roles import SYSTEM_RULES, ROLES, REPORT_SECTIONS, TARGET_PRICE_RULES, report_outline


def cmd_prepare(code: str) -> int:
    print(f"[AShareAgent] 抓取 {code} ...")
    payload = prepare(code)
    print(f"[OK] JSON: {payload['json_path']}")
    print(f"[OK] 上下文: {payload['context_path']}")
    tech = payload.get("technicals") or {}
    if not tech.get("error"):
        print(
            f"[OK] 技术: MA结构={tech.get('ma_structure')} RSI={tech.get('rsi14')} "
            f"MACD={((tech.get('macd') or {}).get('signal'))}"
        )
    ff = payload.get("fund_flow") or {}
    if ff.get("error"):
        print(f"[WARN] 资金流: 数据缺失")
    else:
        print(f"[OK] 资金流: {ff.get('source')}")
    notices = payload.get("notices") or []
    if notices and isinstance(notices[0], dict) and notices[0].get("error"):
        print("[WARN] 公告: 数据缺失")
    else:
        print(f"[OK] 公告: {len(notices)} 条")
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
    print(TARGET_PRICE_RULES)
    print()
    print("报告章节：")
    for sec in REPORT_SECTIONS:
        print(f"  {sec}")
    print()
    print("角色明细：")
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


def cmd_outline(code: str) -> int:
    code = normalize_code(code)
    name = ""
    json_path = DATA_DIR / f"{code}.json"
    if json_path.exists():
        try:
            import json

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            name = str(((payload.get("basic") or {}).get("name")) or "")
        except Exception:
            pass
    text = report_outline(code, name)
    out = REPORTS_DIR / f"{code}_outline.md"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[OK] 大纲已写入: {out}")
    return 0


def cmd_save(code: str, file: str | None = None) -> int:
    """从文件或 stdin 保存报告。"""
    code = normalize_code(code)
    if file:
        markdown = Path(file).read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            print("请通过管道或 --file 提供报告 Markdown，例如：")
            print(f"  python -m ashare_agent save {code} --file report.md")
            return 1
        markdown = sys.stdin.read()
    if not markdown.strip():
        print("报告内容为空")
        return 1
    path = save_report(code, markdown)
    print(f"[OK] 已保存: {path}")
    print(f"[OK] 最新: {REPORTS_DIR / (code + '_latest.md')}")
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

    p4 = sub.add_parser("outline", help="生成空报告大纲")
    p4.add_argument("code", help="股票代码")

    p5 = sub.add_parser("save", help="保存分析报告到 reports/")
    p5.add_argument("code", help="股票代码")
    p5.add_argument("--file", "-f", help="报告 Markdown 文件；省略则读 stdin")

    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args.code)
    if args.cmd == "show":
        return cmd_show(args.code)
    if args.cmd == "roles":
        return cmd_roles()
    if args.cmd == "path":
        return cmd_path(args.code)
    if args.cmd == "outline":
        return cmd_outline(args.code)
    if args.cmd == "save":
        return cmd_save(args.code, getattr(args, "file", None))
    return 2


if __name__ == "__main__":
    sys.exit(main())
