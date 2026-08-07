#!/usr/bin/env python3
"""抓取中证1000增强维度数据包：相对强弱/估值分位/回撤波动/ETF资金。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

from ashare_agent.enrich import fetch_northbound
from ashare_agent.fetch import _df_to_records, _json_safe, _num, _retry, dumps_json
from ashare_agent.indicators import calc_technicals

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def pct_rank(series: pd.Series, value) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty or value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return float((s <= float(value)).mean())


def ret(close: pd.Series, n: int) -> float | None:
    if len(close) <= n:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - n] - 1)


def fmt(v, nd=2):
    try:
        if v is None:
            return "N/A"
        x = float(v)
        if abs(x) >= 1e8:
            return f"{x / 1e8:.2f}亿"
        if abs(x) >= 1e4:
            return f"{x / 1e4:.2f}万"
        return f"{x:.{nd}f}"
    except Exception:
        return str(v)


def pct(v, nd=2):
    try:
        if v is None:
            return "N/A"
        return f"{float(v):.{nd}f}%"
    except Exception:
        return str(v)


def pct_frac(v, nd=2):
    try:
        if v is None:
            return "N/A"
        return f"{float(v) * 100:.{nd}f}%"
    except Exception:
        return str(v)


def pick_index(df_spot: pd.DataFrame, code_sina: str, name: str) -> dict:
    row = df_spot[df_spot["代码"].astype(str) == code_sina]
    if row.empty:
        row = df_spot[df_spot["名称"].astype(str) == name]
    r = row.iloc[0]
    return {
        "code": code_sina.replace("sh", "").replace("sz", ""),
        "symbol": str(r.get("代码")),
        "name": str(r.get("名称")),
        "price": _num(r.get("最新价")),
        "pct_chg": _num(r.get("涨跌幅")),
        "change": _num(r.get("涨跌额")),
        "open": _num(r.get("今开")),
        "high": _num(r.get("最高")),
        "low": _num(r.get("最低")),
        "pre_close": _num(r.get("昨收")),
        "amount": _num(r.get("成交额")),
        "volume": _num(r.get("成交量")),
    }


def index_daily(symbol: str) -> pd.DataFrame:
    df = _retry(lambda: ak.stock_zh_index_daily(symbol=symbol), times=3)
    df = df.tail(260).copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


def fetch_etf_hist(code: str) -> pd.DataFrame:
    sym = f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"
    df = ak.fund_etf_hist_sina(symbol=sym)
    return df.tail(250)


def main() -> None:
    DATA.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    df_spot = _retry(ak.stock_zh_index_spot_sina, times=3)
    idx_defs = [
        ("sh000852", "中证1000"),
        ("sh000300", "沪深300"),
        ("sh000905", "中证500"),
        ("sh000001", "上证指数"),
        ("sz399006", "创业板指"),
        ("sh000688", "科创50"),
    ]
    spots = {name: pick_index(df_spot, code, name) for code, name in idx_defs}
    print("spots", {k: (v["price"], v["pct_chg"]) for k, v in spots.items()})

    dailies = {
        "中证1000": index_daily("sh000852"),
        "沪深300": index_daily("sh000300"),
        "中证500": index_daily("sh000905"),
        "创业板指": index_daily("sz399006"),
        "科创50": index_daily("sh000688"),
    }

    base = dailies["中证1000"][["date", "close", "open", "high", "low", "volume"]].rename(
        columns={"close": "zz1000"}
    )
    rel = base.copy()
    for name, df in dailies.items():
        if name == "中证1000":
            continue
        tmp = df[["date", "close"]].rename(columns={"close": name})
        rel = rel.merge(tmp, on="date", how="inner")

    rel_metrics = {}
    zz = rel["zz1000"]
    for name in ["沪深300", "中证500", "创业板指", "科创50"]:
        if name not in rel.columns:
            continue
        ratio = zz / rel[name]
        abs_r = {f"ret_{n}d": ret(zz, n) for n in (5, 20, 60)}
        peer_r = {f"peer_ret_{n}d": ret(rel[name], n) for n in (5, 20, 60)}
        excess = {}
        for n in (5, 20, 60):
            a, b = abs_r[f"ret_{n}d"], peer_r[f"peer_ret_{n}d"]
            excess[f"excess_{n}d"] = None if a is None or b is None else a - b
        rel_metrics[name] = {
            **abs_r,
            **peer_r,
            **excess,
            "ratio_last": float(ratio.iloc[-1]),
            "ratio_ret_20d": ret(ratio, 20),
            "ratio_ret_60d": ret(ratio, 60),
            "ratio_pct_1y": pct_rank(ratio.tail(244), ratio.iloc[-1]) if len(ratio) >= 60 else None,
        }

    close = dailies["中证1000"]["close"].astype(float)
    dd = close / close.cummax() - 1
    pct_chg_s = close.pct_change()
    corr_hs300_60 = float(
        pd.concat(
            [
                dailies["中证1000"].set_index("date")["close"].pct_change(),
                dailies["沪深300"].set_index("date")["close"].pct_change(),
            ],
            axis=1,
        )
        .dropna()
        .tail(60)
        .corr()
        .iloc[0, 1]
    )
    risk_metrics = {
        "max_dd_60d": float(dd.tail(60).min()),
        "max_dd_120d": float(dd.tail(120).min()),
        "vol_20d_ann": float(pct_chg_s.tail(20).std() * np.sqrt(252)),
        "vol_60d_ann": float(pct_chg_s.tail(60).std() * np.sqrt(252)),
        "down_days_20d": int((pct_chg_s.tail(20) < 0).sum()),
        "corr_hs300_60d": corr_hs300_60,
        "ret_5d": ret(close, 5),
        "ret_20d": ret(close, 20),
        "ret_60d": ret(close, 60),
    }
    print("risk", risk_metrics)

    hist = dailies["中证1000"].rename(
        columns={
            "date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
        }
    )
    hist["日期"] = hist["日期"].astype(str)
    records = _df_to_records(hist.tail(250), limit=250)
    tech = calc_technicals(records)
    tech["recent_60d_low"] = float(hist.tail(60)["最低"].min())
    tech["recent_60d_high"] = float(hist.tail(60)["最高"].max())
    tech["ret_5d"] = risk_metrics["ret_5d"]
    tech["ret_20d"] = risk_metrics["ret_20d"]
    tech["ret_60d"] = risk_metrics["ret_60d"]
    spot = spots["中证1000"]
    tech["spot_price"] = spot["price"]
    tech["spot_pct_chg"] = spot["pct_chg"]

    valuation = {}
    for name in ["中证1000", "沪深300", "中证500"]:
        pe = ak.stock_index_pe_lg(symbol=name)
        pb = ak.stock_index_pb_lg(symbol=name)
        pe["日期"] = pd.to_datetime(pe["日期"])
        pb["日期"] = pd.to_datetime(pb["日期"])
        pe = pe.sort_values("日期")
        pb = pb.sort_values("日期")
        pe_ttm = pe["滚动市盈率"]
        pb_v = pb["市净率"]
        last_pe = float(pe_ttm.iloc[-1])
        last_pb = float(pb_v.iloc[-1])
        valuation[name] = {
            "asof": str(pe["日期"].iloc[-1].date()),
            "index_level": _num(pe["指数"].iloc[-1]),
            "pe_ttm": last_pe,
            "pe_static": float(pe["静态市盈率"].iloc[-1]),
            "pe_ttm_median": _num(pe["滚动市盈率中位数"].iloc[-1]),
            "pb": last_pb,
            "pb_median": _num(pb["市净率中位数"].iloc[-1]),
            "pe_ttm_pct_1y": pct_rank(pe_ttm.tail(244), last_pe),
            "pe_ttm_pct_5y": pct_rank(pe_ttm.tail(244 * 5), last_pe),
            "pb_pct_1y": pct_rank(pb_v.tail(244), last_pb),
            "pb_pct_5y": pct_rank(pb_v.tail(244 * 5), last_pb),
            "pe_ttm_1y_min": float(pe_ttm.tail(244).min()),
            "pe_ttm_1y_max": float(pe_ttm.tail(244).max()),
            "pb_1y_min": float(pb_v.tail(244).min()),
            "pb_1y_max": float(pb_v.tail(244).max()),
        }
        time.sleep(0.3)
    valuation["relative"] = {
        "zz1000_pe_over_hs300": valuation["中证1000"]["pe_ttm"] / valuation["沪深300"]["pe_ttm"],
        "zz1000_pb_over_hs300": valuation["中证1000"]["pb"] / valuation["沪深300"]["pb"],
    }
    print("valuation zz1000", valuation["中证1000"])

    etf_spot = _retry(ak.fund_etf_spot_em, times=3)
    mask_all = etf_spot["名称"].astype(str).str.contains("中证1000", na=False)
    cands = etf_spot[mask_all].copy().sort_values("成交额", ascending=False)
    print("etf count", len(cands))

    representatives = []
    high_liq = []
    for i, (_, er) in enumerate(cands.head(12).iterrows()):
        code = str(er["代码"]).zfill(6)
        name = str(er["名称"])
        item = {
            "code": code,
            "name": name,
            "latest_price": _num(er.get("最新价")),
            "pct_chg": _num(er.get("涨跌幅")),
            "amount": _num(er.get("成交额")),
            "total_market_value": _num(er.get("总市值")),
            "main_net_inflow": _num(er.get("主力净流入-净额")),
            "discount_premium_pct": _num(er.get("基金折价率")),
            "iopv": _num(er.get("IOPV实时估值")),
        }
        high_liq.append(dict(item))
        if i < 6:
            try:
                hdf = fetch_etf_hist(code)
                colmap = {}
                for c in hdf.columns:
                    cs = str(c).lower()
                    if cs in ("date", "日期"):
                        colmap[c] = "日期"
                    elif cs in ("open", "开盘"):
                        colmap[c] = "开盘"
                    elif cs in ("high", "最高"):
                        colmap[c] = "最高"
                    elif cs in ("low", "最低"):
                        colmap[c] = "最低"
                    elif cs in ("close", "收盘"):
                        colmap[c] = "收盘"
                    elif cs in ("volume", "成交量"):
                        colmap[c] = "成交量"
                    elif cs == "amount":
                        colmap[c] = "成交额"
                hdf2 = hdf.rename(columns=colmap)
                recs = _df_to_records(hdf2.tail(250), limit=250)
                t = calc_technicals(recs)
                h60 = hdf2.tail(60)
                item.update(
                    {
                        "ma5": t.get("ma5"),
                        "ma20": t.get("ma20"),
                        "ma60": t.get("ma60"),
                        "macd_signal": (t.get("macd") or {}).get("signal"),
                        "rsi14": t.get("rsi14"),
                        "boll": t.get("boll"),
                        "atr14": t.get("atr14"),
                        "atr14_pct": t.get("atr14_pct"),
                        "kdj": t.get("kdj"),
                        "ma_structure": t.get("ma_structure"),
                        "recent_60d_low": float(pd.to_numeric(h60["最低"], errors="coerce").min()),
                        "recent_60d_high": float(pd.to_numeric(h60["最高"], errors="coerce").max()),
                    }
                )
                cclose = pd.to_numeric(hdf2["收盘"], errors="coerce").dropna()
                if len(cclose) >= 21:
                    item["ret_20d"] = float(cclose.iloc[-1]) / float(cclose.iloc[-21]) - 1
                if len(cclose) >= 61:
                    item["ret_60d"] = float(cclose.iloc[-1]) / float(cclose.iloc[-61]) - 1
                if "成交额" in hdf2.columns:
                    amt = pd.to_numeric(hdf2["成交额"], errors="coerce")
                    item["amount_ma5"] = _num(amt.tail(5).mean())
                    item["amount_ma20"] = _num(amt.tail(20).mean())
                print("etf ok", code, item["latest_price"], item["pct_chg"], item.get("rsi14"))
            except Exception as e:
                item["tech_error"] = str(e)
                print("etf fail", code, e)
            time.sleep(0.4)
        representatives.append(item)

    etf_flow: dict = {"note": "尝试抓取512100份额/净值序列"}
    try:
        info = ak.fund_etf_fund_info_em(fund="512100")
        etf_flow["info_columns"] = list(info.columns)
        etf_flow["info_tail"] = _df_to_records(info.tail(10), limit=10)
        print("etf info cols", list(info.columns))
    except Exception as e:
        etf_flow["info_error"] = str(e)
        print("etf info err", e)

    plain = [
        x
        for x in high_liq
        if "增强" not in x["name"] and "价值" not in x["name"] and "成长" not in x["name"]
    ]
    etf_agg = {
        "plain_count": len(plain),
        "plain_total_mv": sum(x["total_market_value"] or 0 for x in plain),
        "plain_total_amount": sum(x["amount"] or 0 for x in plain),
        "plain_total_main_inflow": sum(x["main_net_inflow"] or 0 for x in plain),
        "top_code": plain[0]["code"] if plain else None,
    }

    # 大盘环境：直接复用已抓同业快照，避免二次新浪全市场请求卡住
    market_env = {
        "source": "sina_index_spot_reuse",
        "asof": ts,
        "indices": [
            {
                "name": name,
                "code": s.get("symbol"),
                "price": s.get("price"),
                "pct_chg": s.get("pct_chg"),
                "amount": s.get("amount"),
            }
            for name, s in spots.items()
        ],
    }
    try:
        nb = fetch_northbound("512100")
    except Exception as e:
        nb = {"error": str(e), "note": "北向抓取失败，报告标注数据缺失"}
    reps_with_tech = [x for x in representatives if x.get("ma20") is not None][:6]
    if len(reps_with_tech) < 3:
        reps_with_tech = representatives[:6]

    payload = {
        "generated_at": ts,
        "theme": "中证1000系列",
        "ai_backend": "cursor-only",
        "note": "增强维度：相对强弱、估值分位、回撤波动、ETF资金/折溢价",
        "index": {**spot, "technicals": tech, "daily_tail": records[-15:]},
        "peer_spots": spots,
        "relative_strength": rel_metrics,
        "risk_metrics": risk_metrics,
        "valuation": valuation,
        "representatives": reps_with_tech,
        "high_liquidity_candidates": high_liq[:12],
        "etf_aggregate": etf_agg,
        "etf_flow": etf_flow,
        "market_env": market_env,
        "northbound": nb,
        "missing_notes": [
            "指数成分行业权重/拥挤度明细未抓取",
            "ETF官方份额申赎日频若接口无则用规模与主力净流入代理",
            "社媒系列口径缺失",
            "个券北向不适用",
        ],
    }
    payload = _json_safe(payload)

    lines = [
        "# 中证1000系列分析数据包（增强维度）",
        "",
        f"- 生成时间: {ts}",
        "- 主题: 中证1000（指数 + 场内 ETF）",
        "- AI 后端: 仅 Cursor（无外部 LLM API）",
        "- 增强维度: 相对强弱 / 估值分位 / 回撤波动 / ETF资金与折溢价",
        "",
        "## 指数快照（中证1000 / 000852）",
        "",
        f"- 最新价: {fmt(spot['price'], 4)}，日涨跌幅: {pct(spot['pct_chg'])}，涨跌额: {fmt(spot['change'], 4)}",
        f"- 今开/最高/最低/昨收: {fmt(spot['open'], 4)} / {fmt(spot['high'], 4)} / {fmt(spot['low'], 4)} / {fmt(spot['pre_close'], 4)}",
        f"- 成交额: {fmt(spot['amount'])}",
        "",
        "## 指数技术锚点",
        "",
    ]
    macd = tech.get("macd") or {}
    boll = tech.get("boll") or {}
    kdj = tech.get("kdj") or {}
    lines += [
        f"- 日线末收(hist): {fmt(tech.get('last_close'), 4)}；现价(spot): {fmt(tech.get('spot_price'), 4)}",
        f"- MA5/MA20/MA60: {fmt(tech.get('ma5'), 4)} / {fmt(tech.get('ma20'), 4)} / {fmt(tech.get('ma60'), 4)}；结构: {tech.get('ma_structure')}",
        f"- MACD: DIF={fmt(macd.get('dif'), 4)} DEA={fmt(macd.get('dea'), 4)} 柱={fmt(macd.get('hist'), 4)}；信号: {macd.get('signal')}",
        f"- RSI14: {fmt(tech.get('rsi14'))}（{tech.get('rsi14_zone')}）",
        f"- 布林: 下轨 {fmt(boll.get('lower'), 4)} / 中轨 {fmt(boll.get('mid'), 4)} / 上轨 {fmt(boll.get('upper'), 4)}；zone={boll.get('zone')} pos={fmt(boll.get('position'), 3)}",
        f"- ATR14: {fmt(tech.get('atr14'), 4)}（约 {pct_frac(tech.get('atr14_pct'))}）",
        f"- KDJ: K={fmt(kdj.get('k'))} / D={fmt(kdj.get('d'))} / J={fmt(kdj.get('j'))}",
        f"- 近60日区间: {fmt(tech.get('recent_60d_low'), 4)} – {fmt(tech.get('recent_60d_high'), 4)}",
        f"- 近5/20/60日涨跌: {pct_frac(tech.get('ret_5d'))} / {pct_frac(tech.get('ret_20d'))} / {pct_frac(tech.get('ret_60d'))}",
        "",
        "## 相对强弱（中证1000 vs 同业）",
        "",
        "| 对照指数 | 自身近5/20/60日 | 对照近5/20/60日 | 超额5/20/60日 | 相对比率20/60日变化 | 相对比率近1年分位 |",
        "|---|---|---|---|---|---|",
    ]
    for name, m in rel_metrics.items():
        lines.append(
            f"| {name} | {pct_frac(m.get('ret_5d'))}/{pct_frac(m.get('ret_20d'))}/{pct_frac(m.get('ret_60d'))} | "
            f"{pct_frac(m.get('peer_ret_5d'))}/{pct_frac(m.get('peer_ret_20d'))}/{pct_frac(m.get('peer_ret_60d'))} | "
            f"{pct_frac(m.get('excess_5d'))}/{pct_frac(m.get('excess_20d'))}/{pct_frac(m.get('excess_60d'))} | "
            f"{pct_frac(m.get('ratio_ret_20d'))}/{pct_frac(m.get('ratio_ret_60d'))} | {pct_frac(m.get('ratio_pct_1y'))} |"
        )

    lines += [
        "",
        "## 估值分位（乐咕/公开口径）",
        "",
        "| 指数 | 数据日 | PE(TTM) | PE一年分位 | PE五年分位 | PB | PB一年分位 | PB五年分位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ["中证1000", "沪深300", "中证500"]:
        v = valuation[name]
        lines.append(
            f"| {name} | {v['asof']} | {fmt(v['pe_ttm'])} | {pct_frac(v['pe_ttm_pct_1y'])} | {pct_frac(v['pe_ttm_pct_5y'])} | "
            f"{fmt(v['pb'])} | {pct_frac(v['pb_pct_1y'])} | {pct_frac(v['pb_pct_5y'])} |"
        )
    rv = valuation["relative"]
    lines += [
        f"- 中证1000 PE(TTM)/沪深300: {fmt(rv.get('zz1000_pe_over_hs300'), 2)}x",
        f"- 中证1000 PB/沪深300: {fmt(rv.get('zz1000_pb_over_hs300'), 2)}x",
        f"- 中证1000 PE一年区间: {fmt(valuation['中证1000']['pe_ttm_1y_min'])} – {fmt(valuation['中证1000']['pe_ttm_1y_max'])}",
        f"- 中证1000 PB一年区间: {fmt(valuation['中证1000']['pb_1y_min'])} – {fmt(valuation['中证1000']['pb_1y_max'])}",
        "",
        "## 回撤与波动",
        "",
        f"- 近60日最大回撤: {pct_frac(risk_metrics['max_dd_60d'])}",
        f"- 近120日最大回撤: {pct_frac(risk_metrics['max_dd_120d'])}",
        f"- 年化波动(20日/60日): {pct_frac(risk_metrics['vol_20d_ann'])} / {pct_frac(risk_metrics['vol_60d_ann'])}",
        f"- 近20日下跌天数: {risk_metrics['down_days_20d']}/20",
        f"- 与沪深300近60日收益相关: {fmt(risk_metrics['corr_hs300_60d'], 3)}",
        "",
        "## 同业现价快照",
        "",
    ]
    for name, s in spots.items():
        lines.append(
            f"- {name}: {fmt(s['price'], 4)}，涨跌幅 {pct(s['pct_chg'])}，成交额 {fmt(s['amount'])}"
        )

    lines += [
        "",
        "## 指数近几日行情（日线）",
        "",
        "| 日期 | 开盘 | 最高 | 最低 | 收盘 |",
        "|---|---:|---:|---:|---:|",
    ]
    for rec in records[-12:]:
        lines.append(
            f"| {rec.get('日期', '')} | {fmt(rec.get('开盘'), 4)} | {fmt(rec.get('最高'), 4)} | "
            f"{fmt(rec.get('最低'), 4)} | {fmt(rec.get('收盘'), 4)} |"
        )

    lines += [
        "",
        "## 代表 ETF 与技术锚点",
        "",
        "| 代码 | 名称 | 最新价 | 日涨跌幅 | 成交额 | 总市值 | 主力净流入 | 折溢价 | MA5 | MA20 | MA60 | MACD | RSI14 | 布林区间 | ATR14 | KDJ | 近60日区间 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---:|---|---|",
    ]
    for it in reps_with_tech:
        boll = it.get("boll") or {}
        kdj = it.get("kdj") or {}
        boll_s = f"{fmt(boll.get('lower'))}-{fmt(boll.get('upper'))}" if boll else "N/A"
        kdj_s = (
            f"K{fmt(kdj.get('k'))}/D{fmt(kdj.get('d'))}/J{fmt(kdj.get('j'))}" if kdj else "N/A"
        )
        rng = f"{fmt(it.get('recent_60d_low'))}-{fmt(it.get('recent_60d_high'))}"
        lines.append(
            f"| {it['code']} | {it['name']} | {fmt(it.get('latest_price'))} | {pct(it.get('pct_chg'))} | "
            f"{fmt(it.get('amount'))} | {fmt(it.get('total_market_value'))} | {fmt(it.get('main_net_inflow'))} | "
            f"{pct(it.get('discount_premium_pct'))} | {fmt(it.get('ma5'))} | {fmt(it.get('ma20'))} | "
            f"{fmt(it.get('ma60'))} | {it.get('macd_signal') or 'N/A'} | {fmt(it.get('rsi14'))} | "
            f"{boll_s} | {fmt(it.get('atr14'))} | {kdj_s} | {rng} |"
        )

    lines += [
        "",
        "## ETF 资金与产品面（聚合）",
        "",
        f"- 宽基中证1000 ETF（剔除增强/价值/成长子类）数量: {etf_agg['plain_count']}",
        f"- 合计总市值: {fmt(etf_agg['plain_total_mv'])}",
        f"- 合计成交额: {fmt(etf_agg['plain_total_amount'])}",
        f"- 合计主力净流入: {fmt(etf_agg['plain_total_main_inflow'])}",
        f"- 龙头: {etf_agg['top_code']}",
        "",
        "## 系列高成交候选",
        "",
    ]
    for it in high_liq[:10]:
        lines.append(
            f"- {it['code']} {it['name']}: 成交额 {fmt(it.get('amount'))}，总市值 {fmt(it.get('total_market_value'))}，"
            f"主力净流入 {fmt(it.get('main_net_inflow'))}，涨跌幅 {pct(it.get('pct_chg'))}，折溢价 {pct(it.get('discount_premium_pct'))}。"
        )

    lines += ["", "## 大盘环境", ""]
    me = market_env or {}
    if me.get("error"):
        lines.append(f"- 数据缺失: {me.get('error')}")
    else:
        lines.append(f"- 来源: {me.get('source')}；时间: {me.get('asof')}")
        for ix in me.get("indices") or []:
            lines.append(
                f"- {ix.get('name')}: {fmt(ix.get('price'), 4)}，涨跌幅 {pct(ix.get('pct_chg'))}"
            )

    lines += ["", "## 北向/沪深港通摘要", ""]
    nbx = payload.get("northbound") or {}
    if nbx.get("error") and not nbx.get("market_summary"):
        lines.append(f"- 数据缺失: {nbx.get('error')}")
    else:
        ms = nbx.get("market_summary") or nbx
        lines.append(f"- 摘要字段: {json.dumps(ms, ensure_ascii=False)[:2000]}")
        if nbx.get("error"):
            lines.append(f"- 个券/明细提示: {nbx.get('error')}")

    lines += ["", "## ETF份额/净值接口摘录", ""]
    if etf_flow.get("info_error"):
        lines.append(
            f"- fund_etf_fund_info_em 失败: {etf_flow['info_error']}；报告用规模与主力净流入代理。"
        )
    elif etf_flow.get("info_tail"):
        lines.append(f"- 列: {etf_flow.get('info_columns')}")
        lines.append(
            f"- 最近记录: {json.dumps(etf_flow['info_tail'][:5], ensure_ascii=False)[:1500]}"
        )

    lines += [
        "",
        "## 缺失与限制",
        "",
        "- 成分行业权重、拥挤度因子明细未抓取，不编造。",
        "- ETF 官方日频份额若接口字段不含份额，用总市值与主力净流入作代理。",
        "- 社媒对系列无统一口径；个股财报/股东户数不适用。",
        "- 目标价以 512100 为交易锚点；指数点位作研究对照。",
        "",
        "## 分析硬约束（给 Cursor）",
        "",
        "1. 禁止编造；缺失写“数据缺失”。",
        "2. 八段结构；第8节含三情景目标价 + decision JSON。",
        "3. 必须引用：相对强弱、估值分位、回撤波动、ETF资金/折溢价（本数据包新增）。",
        "4. action 仅买入/持有/卖出；观望用持有并说明。",
        "5. 研究学习用途，不构成投资建议。",
    ]

    (DATA / "zz1000_series.json").write_text(dumps_json(payload), encoding="utf-8")
    (DATA / "zz1000_series_context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("WROTE enriched package at", ts)
    print("spot", spot)


if __name__ == "__main__":
    main()
