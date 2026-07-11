"""补充公开数据：大盘环境、行业语境、股东户数、北向资金。

全部为本地 AkShare / 公开行情接口，不调用外部大模型。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from .fetch import (
    _df_to_records,
    _num,
    _retry,
    normalize_code,
)


def fetch_market_env() -> Dict[str, Any]:
    """主要指数环境（优先新浪，东财个指兜底，北向摘要再兜底）。"""
    import akshare as ak

    wanted = ["上证指数", "深证成指", "创业板指", "科创50", "沪深300"]
    errors: List[str] = []
    indices: List[Dict[str, Any]] = []

    try:
        df = _retry(ak.stock_zh_index_spot_sina, times=2, sleep_s=1.0)
        if df is not None and not df.empty:
            for name in wanted:
                row = df[df["名称"].astype(str) == name]
                if row.empty:
                    continue
                r = row.iloc[0]
                indices.append(
                    {
                        "name": name,
                        "code": str(r.get("代码", "")),
                        "price": _num(r.get("最新价")),
                        "pct_chg": _num(r.get("涨跌幅")),
                        "change": _num(r.get("涨跌额")),
                        "amount": _num(r.get("成交额")),
                        "volume": _num(r.get("成交量")),
                    }
                )
            if indices:
                return {"source": "sina_index_spot", "indices": indices, "asof": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        errors.append(f"sina_index: {e}")

    # 东财单指数兜底
    try:
        from curl_cffi import requests as cffi_requests

        secids = [
            ("1.000001", "上证指数"),
            ("0.399001", "深证成指"),
            ("0.399006", "创业板指"),
            ("1.000300", "沪深300"),
            ("1.000688", "科创50"),
        ]
        for secid, name in secids:
            try:
                resp = cffi_requests.get(
                    "https://push2.eastmoney.com/api/qt/stock/get",
                    params={"secid": secid, "fields": "f57,f58,f43,f169,f170,f48"},
                    headers={"Referer": "https://quote.eastmoney.com/"},
                    impersonate="chrome120",
                    timeout=15,
                )
                if not resp.text.strip().startswith("{"):
                    continue
                d = (resp.json() or {}).get("data") or {}
                price = _num(d.get("f43"))
                pct = _num(d.get("f170"))
                if price is not None:
                    price = price / 100.0
                if pct is not None:
                    pct = pct / 100.0
                indices.append(
                    {
                        "name": name,
                        "code": str(d.get("f57") or secid),
                        "price": price,
                        "pct_chg": pct,
                        "amount": _num(d.get("f48")),
                    }
                )
            except Exception:
                continue
        if indices:
            return {
                "source": "eastmoney_index_quote",
                "indices": indices,
                "asof": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "errors": errors or None,
            }
    except Exception as e:
        errors.append(f"em_index: {e}")

    # 北向摘要里常带指数涨跌幅
    try:
        df = _retry(ak.stock_hsgt_fund_flow_summary_em, times=2)
        if df is not None and not df.empty:
            seen = set()
            for _, r in df.iterrows():
                name = str(r.get("相关指数") or "")
                if not name or name in seen:
                    continue
                seen.add(name)
                indices.append(
                    {
                        "name": name,
                        "pct_chg": _num(r.get("指数涨跌幅")),
                        "trade_date": str(r.get("交易日") or ""),
                    }
                )
            if indices:
                return {
                    "source": "hsgt_summary_index_pct",
                    "indices": indices,
                    "asof": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "errors": errors or None,
                }
    except Exception as e:
        errors.append(f"hsgt_index_fallback: {e}")

    return {"error": " | ".join(errors) if errors else "大盘环境不可用", "note": "报告中应标注数据缺失"}


def fetch_industry_context(code: str) -> Dict[str, Any]:
    """主营/行业语境 + 尽力抓取行业板块涨跌与资金。"""
    import akshare as ak

    code = normalize_code(code)
    out: Dict[str, Any] = {"code": code}
    errors: List[str] = []

    # 同花顺主营
    try:
        zy = _retry(lambda: ak.stock_zyjs_ths(symbol=code), times=2)
        if zy is not None and not zy.empty:
            r = zy.iloc[0]
            out["business"] = {
                "main_business": str(r.get("主营业务") or ""),
                "product_type": str(r.get("产品类型") or ""),
                "product_name": str(r.get("产品名称") or ""),
            }
            text = " ".join(out["business"].values())
            # 粗映射到东财行业板块名（公开口径，仅作相对强弱参考）
            mapping = [
                ("煤", "煤炭行业"),
                ("电力", "电力行业"),
                ("光伏", "光伏设备"),
                ("风电", "风电设备"),
                ("铝", "铝"),
                ("有色", "小金属"),
                ("银行", "银行"),
                ("白酒", "白酒"),
                ("证券", "证券"),
                ("半导体", "半导体"),
            ]
            guessed = []
            for kw, board in mapping:
                if kw in text and board not in guessed:
                    guessed.append(board)
            out["guessed_boards"] = guessed[:3]
    except Exception as e:
        errors.append(f"zyjs: {e}")

    boards = out.get("guessed_boards") or []
    board_stats = []
    end = datetime.now()
    start = end - timedelta(days=45)
    for board in boards:
        item: Dict[str, Any] = {"board": board}
        try:
            hist = _retry(
                lambda b=board: ak.stock_board_industry_hist_em(
                    symbol=b,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    period="日k",
                    adjust="",
                ),
                times=2,
                sleep_s=1.2,
            )
            if hist is not None and not hist.empty:
                last = hist.iloc[-1]
                # 近20日涨跌
                closes = None
                for c in ("收盘", "收盘价", "close"):
                    if c in hist.columns:
                        closes = pd.to_numeric(hist[c], errors="coerce")
                        break
                ret20 = None
                if closes is not None and len(closes.dropna()) >= 21:
                    s = closes.dropna()
                    ret20 = float(s.iloc[-1] / s.iloc[-21] - 1)
                item["latest"] = {
                    "date": str(last.get("日期") or last.get("date") or ""),
                    "close": _num(last.get("收盘") or last.get("收盘价")),
                    "pct_chg": _num(last.get("涨跌幅")),
                }
                item["ret_20d"] = ret20
                item["source"] = "board_industry_hist_em"
        except Exception as e:
            item["hist_error"] = str(e)

        # 同行成分估值抽样（前若干只）
        try:
            cons = _retry(lambda b=board: ak.stock_board_industry_cons_em(symbol=b), times=2, sleep_s=1.2)
            if cons is not None and not cons.empty:
                # 找本票排名
                code_col = "代码" if "代码" in cons.columns else None
                rank = None
                self_row = None
                if code_col:
                    matches = cons.index[cons[code_col].astype(str).str.zfill(6) == code].tolist()
                    if matches:
                        rank = int(matches[0]) + 1
                        self_row = cons.iloc[matches[0]].to_dict()
                # 抽样字段
                keep_cols = [c for c in ("代码", "名称", "最新价", "涨跌幅", "市盈率-动态", "市净率", "总市值") if c in cons.columns]
                sample = _df_to_records(cons[keep_cols].head(8) if keep_cols else cons.head(8), limit=8)
                pe_series = None
                if "市盈率-动态" in cons.columns:
                    pe_series = pd.to_numeric(cons["市盈率-动态"], errors="coerce")
                item["universe"] = int(len(cons))
                item["self_rank_by_table_order"] = rank
                item["self_row"] = {
                    k: self_row.get(k)
                    for k in ("代码", "名称", "最新价", "涨跌幅", "市盈率-动态", "市净率", "总市值")
                    if self_row and k in self_row
                } if self_row else None
                item["peer_sample"] = sample
                if pe_series is not None and pe_series.notna().any():
                    item["peer_pe_median"] = float(pe_series.median(skipna=True))
                    item["peer_pe_mean"] = float(pe_series.mean(skipna=True))
        except Exception as e:
            item["cons_error"] = str(e)
        board_stats.append(item)

    if board_stats:
        out["boards"] = board_stats
    if errors:
        out["errors"] = errors
    if not out.get("business") and not board_stats:
        out["error"] = " | ".join(errors) if errors else "行业语境不可用"
        out["note"] = "报告中应标注数据缺失"
    return out


def fetch_holders(code: str, limit: int = 6) -> Dict[str, Any]:
    """股东户数变化（东财）。"""
    import akshare as ak

    code = normalize_code(code)
    try:
        df = _retry(lambda: ak.stock_zh_a_gdhs_detail_em(symbol=code), times=2)
        if df is None or df.empty:
            return {"error": "无股东户数", "note": "数据缺失"}
        if "股东户数统计截止日" in df.columns:
            df = df.copy()
            df["_d"] = pd.to_datetime(df["股东户数统计截止日"], errors="coerce")
            df = df.sort_values("_d", ascending=False).drop(columns=["_d"])
        records = _df_to_records(df.head(limit), limit=limit)
        latest = records[0] if records else {}
        summary = {
            "asof": latest.get("股东户数统计截止日"),
            "holders": _num(latest.get("股东户数-本次")),
            "holders_prev": _num(latest.get("股东户数-上次")),
            "holders_chg": _num(latest.get("股东户数-增减")),
            "holders_chg_pct": _num(latest.get("股东户数-增减比例")),
            "avg_mv_per_account": _num(latest.get("户均持股市值")),
            "avg_shares_per_account": _num(latest.get("户均持股数量")),
            "period_pct_chg": _num(latest.get("区间涨跌幅")),
        }
        return {"source": "eastmoney_gdhs_detail", "summary": summary, "recent": records}
    except Exception as e:
        return {"error": str(e), "note": "股东户数数据缺失"}


def fetch_northbound(code: str, limit: int = 10) -> Dict[str, Any]:
    """北向/沪深港通：市场摘要 + 个股持股（若可得）。"""
    import akshare as ak

    code = normalize_code(code)
    out: Dict[str, Any] = {"code": code}
    errors: List[str] = []

    try:
        df = _retry(ak.stock_hsgt_fund_flow_summary_em, times=2)
        if df is not None and not df.empty:
            out["market_summary"] = _df_to_records(df, limit=8)
            out["summary_source"] = "hsgt_fund_flow_summary_em"
    except Exception as e:
        errors.append(f"hsgt_summary: {e}")

    try:
        df = _retry(lambda: ak.stock_hsgt_individual_em(symbol=code), times=2, sleep_s=1.5)
        if df is not None and not df.empty:
            if "持股日期" in df.columns:
                df = df.copy()
                df["_d"] = pd.to_datetime(df["持股日期"], errors="coerce")
                df = df.sort_values("_d", ascending=False).drop(columns=["_d"])
            records = _df_to_records(df.head(limit), limit=limit)
            last = records[0] if records else {}
            out["stock_holding"] = {
                "latest": {
                    "date": last.get("持股日期"),
                    "price": _num(last.get("当日收盘价")),
                    "pct_chg": _num(last.get("当日涨跌幅")),
                    "shares": _num(last.get("持股数量")),
                    "mv": _num(last.get("持股市值")),
                    "pct_of_a": _num(last.get("持股数量占A股百分比")),
                    "delta_shares": _num(last.get("今日增持股数")),
                    "delta_cash": _num(last.get("今日增持资金")),
                },
                "recent": records,
                "source": "hsgt_individual_em",
                "note": "若最新日期明显滞后，报告中应标明时效性不足",
            }
    except Exception as e:
        errors.append(f"hsgt_individual: {e}")

    if errors:
        out["errors"] = errors
    if not out.get("market_summary") and not out.get("stock_holding"):
        out["error"] = " | ".join(errors) if errors else "北向数据不可用"
        out["note"] = "报告中应标注数据缺失"
    return out
