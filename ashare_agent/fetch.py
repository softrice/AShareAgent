"""抓取 A 股公开数据，不调用任何外部大模型。"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd

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
        if col in ("日期", "date", "Date", "data_date", "交易日"):
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


def fetch_daily(code: str, days: int = 120) -> list:
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


def calc_technicals(daily: list) -> Dict[str, Any]:
    if not daily or (isinstance(daily[0], dict) and daily[0].get("error")):
        return {"error": "无可用日线"}
    df = pd.DataFrame(daily)
    # normalize column names from akshare
    colmap = {}
    for c in df.columns:
        if c in ("收盘", "close", "Close"):
            colmap[c] = "close"
        elif c in ("开盘", "open"):
            colmap[c] = "open"
        elif c in ("最高", "high"):
            colmap[c] = "high"
        elif c in ("最低", "low"):
            colmap[c] = "low"
        elif c in ("成交量", "volume"):
            colmap[c] = "volume"
        elif c in ("日期", "date"):
            colmap[c] = "date"
    df = df.rename(columns=colmap)
    if "close" not in df.columns:
        return {"error": f"列缺失: {list(df.columns)}"}
    close = df["close"].astype(float)
    out: Dict[str, Any] = {
        "last_close": float(close.iloc[-1]),
        "ma5": float(close.tail(5).mean()) if len(close) >= 5 else None,
        "ma20": float(close.tail(20).mean()) if len(close) >= 20 else None,
        "ma60": float(close.tail(60).mean()) if len(close) >= 60 else None,
        "ret_5d": float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else None,
        "ret_20d": float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else None,
        "volatility_20d": float(close.pct_change().tail(20).std() * (252 ** 0.5)) if len(close) >= 21 else None,
    }
    if out["ma20"]:
        out["bias_ma20"] = float(out["last_close"] / out["ma20"] - 1)
    return out


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
        "social": social,
        "ai_backend": "cursor-only",
        "note": "本数据包不含外部大模型结果；分析由 Cursor Agent 完成。",
    }

    payload = _json_safe(payload)
    json_path = DATA_DIR / f"{code}.json"
    ctx_path = DATA_DIR / f"{code}_context.md"
    json_path.write_text(dumps_json(payload), encoding="utf-8")
    ctx_path.write_text(build_context_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["context_path"] = str(ctx_path)
    return payload


def _fmt_num(v: Any, nd: int = 2) -> str:
    try:
        if v is None:
            return "N/A"
        x = float(v)
        if abs(x) >= 1e8:
            return f"{x/1e8:.2f}亿"
        return f"{x:.{nd}f}"
    except Exception:
        return str(v)


def build_context_markdown(payload: Dict[str, Any]) -> str:
    b = payload.get("basic") or {}
    t = payload.get("technicals") or {}
    s = payload.get("social") or {}
    lines = [
        f"# A股分析数据包 {payload.get('code')}",
        "",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 名称: {b.get('name')}",
        f"- 最新价: {_fmt_num(b.get('price'))}  涨跌幅: {_fmt_num(b.get('pct_chg'))}%",
        f"- PE/PB: {_fmt_num(b.get('pe'))} / {_fmt_num(b.get('pb'))}",
        f"- 总市值/流通市值: {_fmt_num(b.get('total_mv'))} / {_fmt_num(b.get('circ_mv'))}",
        f"- 数据源: {b.get('source') or 'N/A'}",
        "",
        "## 技术摘要",
        f"- MA5/MA20/MA60: {_fmt_num(t.get('ma5'))} / {_fmt_num(t.get('ma20'))} / {_fmt_num(t.get('ma60'))}",
        f"- 5日/20日涨跌: {_pct(t.get('ret_5d'))} / {_pct(t.get('ret_20d'))}",
        f"- 20日年化波动: {_fmt_num(t.get('volatility_20d'), 3)}",
        f"- 相对MA20偏离: {_pct(t.get('bias_ma20'))}",
        "",
        "## 社媒/情绪",
        "```json",
        dumps_json(s),
        "```",
        "",
        "## 财务摘要（原始）",
        "```json",
        dumps_json(payload.get("financial") or [])[:6000],
        "```",
        "",
        "## 新闻（原始）",
        "```json",
        dumps_json(payload.get("news") or [])[:6000],
        "```",
        "",
        "## 近30日行情（原始）",
        "```json",
        dumps_json(payload.get("daily_tail") or [])[:6000],
        "```",
        "",
        "## Cursor 分析指令",
        "请扮演 TradingAgents 风格多智能体，仅基于以上数据输出：",
        "1) 市场/技术面分析师",
        "2) 基本面分析师",
        "3) 新闻分析师",
        "4) 社媒情绪分析师",
        "5) 风险官",
        "6) 综合裁决（含多空要点、失效条件、仓位建议仅作研究用）",
        "要求：有数据引用；不确定就标明；不编造财报数字；中文。",
    ]
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
