"""抓取 A 股公开数据，不调用任何外部大模型。"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd

from .indicators import calc_technicals

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


def _retry(fn: Callable[[], Any], times: int = 3, sleep_s: float = 1.2) -> Any:
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as e:
            last = e
            if i + 1 < times:
                time.sleep(sleep_s * (i + 1))
    raise last  # type: ignore[misc]


def normalize_code(code: str) -> str:
    code = str(code).strip().upper()
    code = code.replace(".SZ", "").replace(".SH", "").replace(".SS", "")
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) < 6:
        raise ValueError(f"股票代码无效: {code}")
    return digits[-6:].zfill(6)


def xq_symbol(code: str) -> str:
    code = normalize_code(code)
    return f"SH{code}" if code.startswith(("5", "6", "9")) else f"SZ{code}"


def market_tag(code: str) -> str:
    """AkShare 常用市场标记：沪 sh / 深 sz。"""
    code = normalize_code(code)
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def em_secid(code: str) -> str:
    code = normalize_code(code)
    # 东财 secid：沪 1.xxxxxx，深 0.xxxxxx
    return f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"


def _normalize_date_series(series: pd.Series) -> pd.Series:
    """把日期列统一成可读字符串（兼容毫秒/秒时间戳与 object dtype）。"""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.astype(str)
    sample = series.dropna()
    if sample.empty:
        return series.astype(str)
    first = sample.iloc[0]
    try:
        num = float(first)
        if num > 1e11:
            return pd.to_datetime(series, unit="ms", errors="coerce").astype(str)
        if num > 1e9:
            return pd.to_datetime(series, unit="s", errors="coerce").astype(str)
    except Exception:
        pass
    return pd.to_datetime(series, errors="coerce").astype(str)


def _df_to_records(df: Optional[pd.DataFrame], limit: int = 30) -> list:
    if df is None or getattr(df, "empty", True):
        return []
    out = df.head(limit).copy()
    for col in list(out.columns):
        if col in (
            "日期",
            "date",
            "Date",
            "data_date",
            "交易日",
            "公告日期",
            "发布时间",
            "股东户数统计截止日",
            "股东户数公告日期",
            "持股日期",
        ):
            out[col] = _normalize_date_series(out[col])
        elif pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str)
    return json.loads(out.to_json(orient="records", force_ascii=False))


def _merge_basic(info: Dict[str, Any], patch: Dict[str, Any]) -> None:
    """只补空字段，不覆盖已有有效值。"""
    for k, v in patch.items():
        if v is None or v == "":
            continue
        cur = info.get(k)
        if cur is None or cur == "" or cur == info.get("code"):
            info[k] = v


def fetch_basic(code: str) -> Dict[str, Any]:
    import akshare as ak

    code = normalize_code(code)
    info: Dict[str, Any] = {"code": code, "name": code}
    errors: list = []
    sources: list = []

    # 1) 东财全市场快照
    try:
        spot = _retry(ak.stock_zh_a_spot_em, times=3)
        row = spot[spot["代码"].astype(str).str.zfill(6) == code]
        if not row.empty:
            r = row.iloc[0]
            _merge_basic(
                info,
                {
                    "name": str(r.get("名称", code)),
                    "price": _num(r.get("最新价")),
                    "pct_chg": _num(r.get("涨跌幅")),
                    "amount": _num(r.get("成交额")),
                    "volume": _num(r.get("成交量")),
                    "pe": _num(r.get("市盈率-动态")),
                    "pb": _num(r.get("市净率")),
                    "total_mv": _num(r.get("总市值")),
                    "circ_mv": _num(r.get("流通市值")),
                    "high": _num(r.get("最高")),
                    "low": _num(r.get("最低")),
                    "open": _num(r.get("今开")),
                },
            )
            sources.append("eastmoney_spot")
    except Exception as e:
        errors.append(f"eastmoney_spot: {e}")

    # 2) 个股信息兜底
    if info.get("price") is None or info.get("pe") is None:
        try:
            df = _retry(lambda: ak.stock_individual_info_em(symbol=code), times=2)
            kv = {str(a): b for a, b in zip(df.iloc[:, 0], df.iloc[:, 1])}
            _merge_basic(
                info,
                {
                    "name": str(kv.get("股票简称") or kv.get("名称") or info["name"]),
                    "price": _num(kv.get("最新") or kv.get("最新价")),
                    "total_mv": _num(kv.get("总市值")),
                    "circ_mv": _num(kv.get("流通市值")),
                    "pe": _num(kv.get("市盈率-动态") or kv.get("市盈率")),
                    "pb": _num(kv.get("市净率")),
                },
            )
            sources.append("eastmoney_individual")
        except Exception as e:
            errors.append(f"individual_info: {e}")

    # 3) 估值序列兜底（PE/PB/市值/涨跌幅）
    if info.get("pe") is None or info.get("pb") is None or info.get("total_mv") is None:
        try:
            vdf = _retry(lambda: ak.stock_value_em(symbol=code), times=2)
            if vdf is not None and not vdf.empty:
                r = vdf.iloc[-1]
                _merge_basic(
                    info,
                    {
                        "price": _num(r.get("当日收盘价")),
                        "pct_chg": _num(r.get("当日涨跌幅")),
                        "pe": _num(r.get("PE(TTM)")),
                        "pb": _num(r.get("市净率")),
                        "total_mv": _num(r.get("总市值")),
                        "circ_mv": _num(r.get("流通市值")),
                    },
                )
                sources.append("eastmoney_value")
        except Exception as e:
            errors.append(f"stock_value_em: {e}")

    if sources:
        info["source"] = "+".join(sources)
    if errors:
        info["spot_error"] = " | ".join(errors)
    return info


def fetch_daily(code: str, days: int = 250) -> list:
    import akshare as ak

    code = normalize_code(code)
    end = datetime.now()
    start = end - timedelta(days=days * 2)
    errors: list = []

    try:
        df = _retry(
            lambda: ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            ),
            times=3,
        )
        if df is not None and not df.empty:
            return _df_to_records(df.tail(days), limit=days)
    except Exception as e:
        errors.append(f"em_hist: {e}")

    # 新浪日线兜底
    try:
        symbol = f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"
        df = _retry(lambda: ak.stock_zh_a_daily(symbol=symbol, adjust="qfq"), times=2)
        if df is not None and not df.empty:
            return _df_to_records(df.tail(days), limit=days)
    except Exception as e:
        errors.append(f"sina_daily: {e}")

    return [{"error": " | ".join(errors) if errors else "无日线"}]


def fetch_financial_abstract(code: str) -> list:
    import akshare as ak

    code = normalize_code(code)
    try:
        df = _retry(
            lambda: ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期"),
            times=2,
        )
        if df is not None and not df.empty:
            # 同花顺接口通常最旧在前，取最近报告期
            if "报告期" in df.columns:
                df = df.copy()
                df["_d"] = pd.to_datetime(df["报告期"], errors="coerce")
                df = df.sort_values("_d", ascending=False).drop(columns=["_d"])
            return _df_to_records(df.head(8), limit=8)
    except Exception:
        pass
    try:
        df = ak.stock_financial_analysis_indicator(symbol=code)
        if df is not None and not df.empty:
            return _df_to_records(df.tail(8).iloc[::-1], limit=8)
    except Exception as e:
        return [{"error": str(e)}]
    return [{"error": "财务数据不可用"}]


def _strip_em_tags(text: Any) -> str:
    """清理东财新闻 HTML 标签；避免 akshare 里 r'\\u3000' 正则触发 pyarrow 报错。"""
    import re

    s = "" if text is None else str(text)
    s = re.sub(r"</?em>", "", s)
    s = s.replace("\u3000", "").replace("\r\n", " ").replace("\n", " ")
    return s.strip()


def _fetch_news_em_raw(symbol: str) -> pd.DataFrame:
    """直接打东财搜索接口，绕过 ak.stock_news_em 的 pyarrow 正则 bug。"""
    from curl_cffi import requests as cffi_requests

    url = "https://search-api-web.eastmoney.com/search/jsonp"
    cb = "jQuery35101792940631092459_1764599530165"
    inner_param = {
        "uid": "",
        "keyword": symbol,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": 15,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    params = {
        "cb": cb,
        "param": json.dumps(inner_param, ensure_ascii=False),
        "_": "1764599530176",
    }
    headers = {
        "Referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        ),
    }
    r = cffi_requests.get(url, params=params, headers=headers, timeout=20)
    text = r.text
    if text.startswith(cb + "(") and text.endswith(")"):
        payload = json.loads(text[len(cb) + 1 : -1])
    else:
        # 兼容 cb 前缀变化
        l = text.find("(")
        rparen = text.rfind(")")
        payload = json.loads(text[l + 1 : rparen])
    rows = (payload.get("result") or {}).get("cmsArticleWebOld") or []
    if not rows:
        return pd.DataFrame()
    temp_df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "关键词": symbol,
            "新闻标题": temp_df.get("title", pd.Series(dtype=str)).map(_strip_em_tags),
            "新闻内容": temp_df.get("content", pd.Series(dtype=str)).map(_strip_em_tags),
            "发布时间": temp_df.get("date", pd.Series(dtype=str)),
            "文章来源": temp_df.get("mediaName", pd.Series(dtype=str)),
            "新闻链接": "http://finance.eastmoney.com/a/"
            + temp_df.get("code", pd.Series(dtype=str)).astype(str)
            + ".html",
        }
    )
    return out


def fetch_news(code: str) -> list:
    import akshare as ak

    code = normalize_code(code)
    errors: list = []

    # 优先自研接口（避开 akshare + pyarrow 的 \\u3000 正则 bug）
    try:
        df = _retry(lambda: _fetch_news_em_raw(code), times=2)
        if df is not None and not df.empty:
            return _df_to_records(df, limit=15)
    except Exception as e:
        errors.append(f"em_raw: {e}")

    try:
        df = _retry(lambda: ak.stock_news_em(symbol=code), times=1)
        if df is not None and not df.empty:
            # 若 akshare 偶发成功，再做一次安全清洗
            for col in ("新闻标题", "新闻内容"):
                if col in df.columns:
                    df[col] = df[col].map(_strip_em_tags)
            return _df_to_records(df, limit=15)
    except Exception as e:
        errors.append(f"ak_news: {e}")

    return [
        {
            "error": " | ".join(errors) if errors else "无新闻",
            "note": "新闻源暂时不可用，报告中应标注数据缺失",
        }
    ]


def fetch_social(code: str) -> Dict[str, Any]:
    import akshare as ak

    code = normalize_code(code)
    xs = xq_symbol(code)
    social: Dict[str, Any] = {"code": code, "xq_symbol": xs}

    try:
        follow_df = ak.stock_hot_follow_xq(symbol="最热门")
        follow_df = follow_df.reset_index(drop=True)
        matches = follow_df.index[follow_df["股票代码"].astype(str).str.endswith(code)].tolist()
        if matches:
            i = matches[0]
            r = follow_df.iloc[i]
            social["xueqiu_follow"] = {
                "symbol": str(r.get("股票代码")),
                "name": str(r.get("股票简称")),
                "follow": _num(r.get("关注")),
                "price": _num(r.get("最新价")),
                "rank": i + 1,
                "universe": len(follow_df),
            }
        else:
            social["xueqiu_follow"] = {"message": f"未在雪球热门关注榜找到 {xs}"}
    except Exception as e:
        social["xueqiu_follow"] = {"error": str(e)}

    try:
        comment_df = ak.stock_comment_em()
        crow = comment_df[comment_df["代码"].astype(str).str.zfill(6) == code]
        if not crow.empty:
            c = crow.iloc[0]
            score = _num(c.get("综合得分")) or 0
            if score >= 70:
                mood = "偏积极"
            elif score >= 55:
                mood = "中性偏多"
            elif score >= 45:
                mood = "中性"
            else:
                mood = "偏谨慎"
            social["eastmoney_guba"] = {
                "name": str(c.get("名称", "")),
                "score": score,
                "focus": _num(c.get("关注指数")),
                "rank": _num(c.get("目前排名")),
                "rise": _num(c.get("上升")),
                "mood": mood,
                "trade_date": str(c.get("交易日", "")),
            }
        else:
            social["eastmoney_guba"] = {"message": f"未找到 {code} 股吧情绪"}
    except Exception as e:
        social["eastmoney_guba"] = {"error": str(e)}

    return social


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except Exception:
        return None


def _json_safe(obj: Any) -> Any:
    """把 numpy/pandas 标量转成原生 JSON 类型。"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return str(obj)
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return _json_safe(obj.item())
        except Exception:
            pass
    if isinstance(obj, float) and pd.isna(obj):
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    return str(obj)


def dumps_json(obj: Any) -> str:
    return json.dumps(_json_safe(obj), ensure_ascii=False, indent=2)


def fetch_fund_flow(code: str, days: int = 20) -> Dict[str, Any]:
    """个股资金流向。优先 AkShare，失败则尝试东财 daykline 接口。"""
    import akshare as ak

    code = normalize_code(code)
    market = market_tag(code)
    errors: list = []

    try:
        df = _retry(
            lambda: ak.stock_individual_fund_flow(stock=code, market=market),
            times=3,
            sleep_s=1.5,
        )
        if df is not None and not df.empty:
            records = _df_to_records(df.tail(days), limit=days)
            last = records[-1] if records else {}
            summary: Dict[str, Any] = {
                "latest_date": last.get("日期") or last.get("date"),
                "main_net": _num(
                    last.get("主力净流入-净额")
                    or last.get("主力净流入")
                    or last.get("主力净额")
                ),
                "main_net_pct": _num(last.get("主力净流入-净占比")),
                "super_net": _num(last.get("超大单净流入-净额")),
                "big_net": _num(last.get("大单净流入-净额")),
                "mid_net": _num(last.get("中单净流入-净额")),
                "small_net": _num(last.get("小单净流入-净额")),
            }
            main_col = None
            for c in ("主力净流入-净额", "主力净流入", "主力净额"):
                if c in df.columns:
                    main_col = c
                    break
            if main_col:
                try:
                    summary["main_net_sum_period"] = float(
                        pd.to_numeric(df.tail(days)[main_col], errors="coerce").sum()
                    )
                    summary["period_days"] = min(days, len(df))
                except Exception:
                    pass
            return {
                "source": "akshare_individual_fund_flow",
                "summary": summary,
                "recent": records,
            }
    except Exception as e:
        errors.append(f"ak_fund_flow: {e}")

    try:
        from curl_cffi import requests as cffi_requests

        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "lmt": "0",
            "klt": "101",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "secid": em_secid(code),
            "ut": "b2884a393a59ad64002292a3e90d522a",
        }
        headers = {
            "Referer": "https://data.eastmoney.com/zjlx/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        r = cffi_requests.get(
            url, params=params, headers=headers, impersonate="chrome120", timeout=20
        )
        payload = r.json()
        klines = ((payload.get("data") or {}).get("klines")) or []
        if klines:
            rows = []
            for line in klines[-days:]:
                parts = str(line).split(",")
                if len(parts) < 6:
                    continue
                rows.append(
                    {
                        "日期": parts[0],
                        "主力净流入-净额": _num(parts[1]),
                        "小单净流入-净额": _num(parts[2]),
                        "中单净流入-净额": _num(parts[3]),
                        "大单净流入-净额": _num(parts[4]),
                        "超大单净流入-净额": _num(parts[5]),
                    }
                )
            if rows:
                last = rows[-1]
                summary = {
                    "latest_date": last.get("日期"),
                    "main_net": last.get("主力净流入-净额"),
                    "super_net": last.get("超大单净流入-净额"),
                    "big_net": last.get("大单净流入-净额"),
                    "mid_net": last.get("中单净流入-净额"),
                    "small_net": last.get("小单净流入-净额"),
                    "main_net_sum_period": sum(
                        (x.get("主力净流入-净额") or 0) for x in rows
                    ),
                    "period_days": len(rows),
                }
                return {
                    "source": "eastmoney_fflow_daykline",
                    "summary": summary,
                    "recent": rows,
                }
    except Exception as e:
        errors.append(f"em_fflow: {e}")

    return {
        "error": " | ".join(errors) if errors else "资金流不可用",
        "note": "资金流数据缺失，报告中应标注数据缺失，勿编造",
    }


def fetch_notices(code: str, days: int = 45, limit: int = 20) -> list:
    """公司公告（东财个股公告）。"""
    import akshare as ak

    code = normalize_code(code)
    end = datetime.now()
    start = end - timedelta(days=days)
    begin_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    errors: list = []

    try:
        df = _retry(
            lambda: ak.stock_individual_notice_report(
                security=code, begin_date=begin_s, end_date=end_s
            ),
            times=2,
            sleep_s=1.5,
        )
        if df is not None and not df.empty:
            keep = [
                c
                for c in ("代码", "名称", "公告标题", "公告类型", "公告日期", "网址")
                if c in df.columns
            ]
            out = df[keep].copy() if keep else df.copy()
            if "公告日期" in out.columns:
                out["_d"] = pd.to_datetime(out["公告日期"], errors="coerce")
                out = out.sort_values("_d", ascending=False).drop(columns=["_d"])
            return _df_to_records(out.head(limit), limit=limit)
    except Exception as e:
        errors.append(f"individual_notice: {e}")

    try:
        df = _retry(
            lambda: ak.stock_notice_report(symbol="全部", date=end.strftime("%Y%m%d")),
            times=1,
        )
        if df is not None and not df.empty and "代码" in df.columns:
            crow = df[df["代码"].astype(str).str.zfill(6) == code]
            if not crow.empty:
                return _df_to_records(crow.head(limit), limit=limit)
    except Exception as e:
        errors.append(f"notice_report: {e}")

    return [
        {
            "error": " | ".join(errors) if errors else "无公告",
            "note": "公告源暂时不可用，报告中应标注数据缺失",
        }
    ]


def prepare(code: str) -> Dict[str, Any]:
    """抓取完整数据包，写入 data/ 与 Cursor 可读上下文。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    code = normalize_code(code)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    basic = fetch_basic(code)
    daily = fetch_daily(code)
    tech = calc_technicals(daily)
    financial = fetch_financial_abstract(code)
    news = fetch_news(code)
    social = fetch_social(code)
    fund_flow = fetch_fund_flow(code)
    notices = fetch_notices(code)

    # 延迟导入，避免与 enrich↔fetch 循环依赖
    from .enrich import fetch_market_env, fetch_industry_context, fetch_holders, fetch_northbound

    market_env = fetch_market_env()
    industry = fetch_industry_context(code)
    holders = fetch_holders(code)
    northbound = fetch_northbound(code)

    # 用社媒/日线补全快照失败时的基本信息
    xq = (social or {}).get("xueqiu_follow") or {}
    guba = (social or {}).get("eastmoney_guba") or {}
    if basic.get("name") in (None, "", code) and xq.get("name"):
        basic["name"] = str(xq["name"])
    elif basic.get("name") in (None, "", code) and guba.get("name"):
        basic["name"] = str(guba["name"])
    if basic.get("price") is None and xq.get("price") is not None:
        basic["price"] = xq.get("price")
        basic["source"] = (basic.get("source") or "") + "+xueqiu_follow_fallback"
    if tech.get("last_close") and basic.get("price") is None:
        basic["price"] = tech["last_close"]
        basic["source"] = (basic.get("source") or "") + "+hist_last_close"
    # 日线最后一根补涨跌幅
    if basic.get("pct_chg") is None and isinstance(daily, list) and daily:
        last = daily[-1]
        if isinstance(last, dict) and not last.get("error"):
            for k in ("涨跌幅", "pct_chg", "changepercent"):
                if last.get(k) is not None:
                    basic["pct_chg"] = _num(last.get(k))
                    break

    payload = {
        "generated_at": ts,
        "code": code,
        "basic": basic,
        "technicals": tech,
        "daily_tail": daily[-30:] if isinstance(daily, list) else daily,
        "financial": financial,
        "news": news,
        "notices": notices,
        "fund_flow": fund_flow,
        "market_env": market_env,
        "industry": industry,
        "holders": holders,
        "northbound": northbound,
        "social": social,
        "ai_backend": "cursor-only",
        "note": "本数据包不含外部大模型结果；分析由 Cursor Agent 完成。",
        "analysis_roles": [
            "market",
            "china_market",
            "fundamental",
            "news",
            "social",
            "bull",
            "bear",
            "risk",
            "trader",
            "judge",
        ],
    }

    payload = _json_safe(payload)
    json_path = DATA_DIR / f"{code}.json"
    ctx_path = DATA_DIR / f"{code}_context.md"
    json_path.write_text(dumps_json(payload), encoding="utf-8")
    ctx_path.write_text(build_context_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["context_path"] = str(ctx_path)
    return payload


def _fmt_num(v: Any, nd: int = 2, plain: bool = False) -> str:
    try:
        if v is None:
            return "N/A"
        x = float(v)
        if not plain:
            if abs(x) >= 1e8:
                return f"{x/1e8:.2f}亿"
            if abs(x) >= 1e4:
                return f"{x/1e4:.2f}万"
        return f"{x:.{nd}f}"
    except Exception:
        return str(v)


def _clip_json(obj: Any, limit: int = 12000) -> str:
    """序列化并安全截断，避免砍断后误导；超长时追加说明。"""
    text = dumps_json(obj)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [已截断，完整数据见同名 JSON；原文长度 {len(text)}]"


def build_context_markdown(payload: Dict[str, Any]) -> str:
    from .roles import REPORT_SECTIONS, SYSTEM_RULES, TARGET_PRICE_RULES
    from .decision import DECISION_RULES, decision_block_example

    b = payload.get("basic") or {}
    t = payload.get("technicals") or {}
    s = payload.get("social") or {}
    macd = t.get("macd") or {}
    boll = t.get("boll") or {}
    kdj = t.get("kdj") or {}
    ff = payload.get("fund_flow") or {}
    ff_sum = ff.get("summary") or {}
    me = payload.get("market_env") or {}
    ind = payload.get("industry") or {}
    holders = payload.get("holders") or {}
    nb = payload.get("northbound") or {}

    lines = [
        f"# A股分析数据包 {payload.get('code')}",
        "",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 名称: {b.get('name')}",
        f"- 最新价: {_fmt_num(b.get('price'))}  涨跌幅: {_fmt_num(b.get('pct_chg'))}%",
        f"- PE/PB: {_fmt_num(b.get('pe'))} / {_fmt_num(b.get('pb'))}",
        f"- 总市值/流通市值: {_fmt_num(b.get('total_mv'))} / {_fmt_num(b.get('circ_mv'))}",
        f"- 数据源: {b.get('source') or 'N/A'}",
        f"- AI 后端: {payload.get('ai_backend') or 'cursor-only'}",
        "",
        "## 大盘环境",
    ]
    if me.get("error"):
        lines.append(f"- 数据缺失: {me.get('error')}")
    else:
        lines.append(f"- 来源: {me.get('source') or 'N/A'}  时间: {me.get('asof') or 'N/A'}")
        for ix in me.get("indices") or []:
            lines.append(
                f"- {ix.get('name')}: {_fmt_num(ix.get('price'), plain=True)}  涨跌幅 {_fmt_num(ix.get('pct_chg'), plain=True)}%"
            )

    lines.append("")
    lines.append("## 行业语境")
    biz = (ind.get("business") or {}) if isinstance(ind, dict) else {}
    if ind.get("error") and not biz:
        lines.append(f"- 数据缺失: {ind.get('error')}")
    else:
        if biz:
            lines.append(f"- 主营: {biz.get('main_business') or 'N/A'}")
            lines.append(f"- 产品: {biz.get('product_type') or 'N/A'}")
        if ind.get("guessed_boards"):
            lines.append(f"- 映射板块: {', '.join(ind.get('guessed_boards') or [])}")
        for board in ind.get("boards") or []:
            lines.append(
                f"- 板块 {board.get('board')}: 近20日 {_pct(board.get('ret_20d'))}; "
                f"同行PE中位数 {_fmt_num(board.get('peer_pe_median'))}; "
                f"成分股数 {board.get('universe') or 'N/A'}"
            )

    hsum = holders.get("summary") or {}
    lines.extend(["", "## 股东户数"])
    if holders.get("error"):
        lines.append(f"- 数据缺失: {holders.get('error')}")
    else:
        lines.append(f"- 截止: {hsum.get('asof') or 'N/A'}")
        lines.append(
            f"- 户数: {_fmt_num(hsum.get('holders'), 0)}（较上次 {_fmt_num(hsum.get('holders_chg'), 0)} / {_fmt_num(hsum.get('holders_chg_pct'))}%）"
        )
        lines.append(f"- 户均持股市值: {_fmt_num(hsum.get('avg_mv_per_account'))}")

    lines.extend(["", "## 北向/沪深港通"])
    if nb.get("error") and not nb.get("market_summary") and not nb.get("stock_holding"):
        lines.append(f"- 数据缺失: {nb.get('error')}")
    else:
        if nb.get("market_summary"):
            lines.append("- 市场摘要已附原始 JSON")
        sh = ((nb.get("stock_holding") or {}).get("latest")) or {}
        if sh:
            lines.append(
                f"- 个股北向最新: {sh.get('date')}  持股占比 {_fmt_num(sh.get('pct_of_a'))}%  "
                f"持股市值 {_fmt_num(sh.get('mv'))}"
            )
            if (nb.get("stock_holding") or {}).get("note"):
                lines.append(f"- 备注: {(nb.get('stock_holding') or {}).get('note')}")

    lines.extend(
        [
            "",
            "## 技术摘要",
            f"- 均线结构: {t.get('ma_structure') or 'N/A'}",
            f"- MA5/MA20/MA60: {_fmt_num(t.get('ma5'))} / {_fmt_num(t.get('ma20'))} / {_fmt_num(t.get('ma60'))}",
            f"- 5日/20日涨跌: {_pct(t.get('ret_5d'))} / {_pct(t.get('ret_20d'))}",
            f"- 20日年化波动: {_fmt_num(t.get('volatility_20d'), 3)}",
            f"- 相对MA20偏离: {_pct(t.get('bias_ma20'))}",
            f"- MACD DIF/DEA/HIST: {_fmt_num(macd.get('dif'), 4)} / {_fmt_num(macd.get('dea'), 4)} / {_fmt_num(macd.get('hist'), 4)}  ({macd.get('signal') or 'N/A'})",
            f"- RSI14: {_fmt_num(t.get('rsi14'))} ({t.get('rsi14_zone') or 'N/A'})",
            f"- 布林 上/中/下: {_fmt_num(boll.get('upper'))} / {_fmt_num(boll.get('mid'))} / {_fmt_num(boll.get('lower'))}  ({boll.get('zone') or 'N/A'})",
            f"- ATR14: {_fmt_num(t.get('atr14'))} ({_pct(t.get('atr14_pct'))} of price)",
            f"- KDJ K/D/J: {_fmt_num(kdj.get('k'))} / {_fmt_num(kdj.get('d'))} / {_fmt_num(kdj.get('j'))}",
            "",
            "## 资金流向摘要",
        ]
    )
    if ff.get("error"):
        lines.append(f"- 数据缺失: {ff.get('error')}")
        if ff.get("note"):
            lines.append(f"- 备注: {ff.get('note')}")
    else:
        lines.extend(
            [
                f"- 来源: {ff.get('source') or 'N/A'}",
                f"- 最近日期: {ff_sum.get('latest_date') or 'N/A'}",
                f"- 主力净流入: {_fmt_num(ff_sum.get('main_net'))}",
                f"- 主力净占比: {_fmt_num(ff_sum.get('main_net_pct'))}%",
                f"- 超大单/大单净流入: {_fmt_num(ff_sum.get('super_net'))} / {_fmt_num(ff_sum.get('big_net'))}",
                f"- 近{ff_sum.get('period_days') or 'N'}日主力净流入合计: {_fmt_num(ff_sum.get('main_net_sum_period'))}",
            ]
        )
    lines.extend(
        [
            "",
            "## 社媒/情绪",
            "```json",
            _clip_json(s, 4000),
            "```",
            "",
            "## 财务摘要（原始）",
            "```json",
            _clip_json(payload.get("financial") or [], 10000),
            "```",
            "",
            "## 新闻（原始）",
            "```json",
            _clip_json(payload.get("news") or [], 10000),
            "```",
            "",
            "## 公司公告（原始）",
            "```json",
            _clip_json(payload.get("notices") or [], 8000),
            "```",
            "",
            "## 资金流向明细（原始）",
            "```json",
            _clip_json(ff, 8000),
            "```",
            "",
            "## 大盘/行业/股东/北向（原始）",
            "```json",
            _clip_json(
                {
                    "market_env": me,
                    "industry": ind,
                    "holders": holders,
                    "northbound": nb,
                },
                14000,
            ),
            "```",
            "",
            "## 近30日行情（原始）",
            "```json",
            _clip_json(payload.get("daily_tail") or [], 10000),
            "```",
            "",
            "## Cursor 分析指令",
            SYSTEM_RULES,
            "",
            TARGET_PRICE_RULES,
            "",
            DECISION_RULES,
            "",
            "终裁 JSON 示例（请按真实分析改写数值，勿照抄）：",
            decision_block_example(),
            "",
            "请扮演 TradingAgents 风格多智能体，仅基于以上数据按下列章节输出中文报告：",
        ]
    )
    for sec in REPORT_SECTIONS:
        lines.append(sec)
    lines.extend(
        [
            "",
            "要求：有数据引用；不确定就标明「数据缺失」；不编造财报/新闻/公告数字；",
            "技术面须引用 MA/MACD/RSI/布林/ATR/KDJ；须引用大盘/行业/股东/北向（若有）；",
            "第8节必须含目标价三情景 + 末尾 decision JSON（action 仅买入/持有/卖出）；报告末尾加免责声明。",
        ]
    )
    return "\n".join(lines)


def _pct(v: Any) -> str:
    try:
        return f"{float(v)*100:.2f}%"
    except Exception:
        return "N/A"


def save_report(code: str, markdown: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    code = normalize_code(code)
    path = REPORTS_DIR / f"{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(markdown, encoding="utf-8")
    latest = REPORTS_DIR / f"{code}_latest.md"
    latest.write_text(markdown, encoding="utf-8")
    return path
