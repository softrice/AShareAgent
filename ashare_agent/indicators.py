"""技术指标（对齐 TradingAgents-CN 常用集合，纯本地计算，无外部 API）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=int(n), adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14, method: str = "china") -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    if method == "china":
        avg_gain = gain.ewm(com=n - 1, adjust=True).mean()
        avg_loss = loss.ewm(com=n - 1, adjust=True).mean()
    else:
        avg_gain = gain.ewm(alpha=1 / float(n), adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / float(n), adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    dif = _ema(close, fast) - _ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea
    return pd.DataFrame({"dif": dif, "dea": dea, "macd_hist": hist})


def _boll(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n, min_periods=1).mean()
    std = close.rolling(n, min_periods=1).std()
    return pd.DataFrame(
        {
            "boll_mid": mid,
            "boll_upper": mid + k * std,
            "boll_lower": mid - k * std,
        }
    )


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / float(n), adjust=False).mean()


def _kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    lowest = low.rolling(n, min_periods=1).min()
    highest = high.rolling(n, min_periods=1).max()
    rsv = (close - lowest) / (highest - lowest).replace(0, pd.NA) * 100
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"kdj_k": k, "kdj_d": d, "kdj_j": j})


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        x = float(v)
        if pd.isna(x):
            return None
        return x
    except Exception:
        return None


def calc_technicals(daily: list) -> Dict[str, Any]:
    """从日线记录计算 MA/MACD/RSI/布林/ATR/KDJ 等摘要。"""
    if not daily or (isinstance(daily[0], dict) and daily[0].get("error")):
        return {"error": "无可用日线"}

    df = pd.DataFrame(daily)
    colmap = {}
    for c in df.columns:
        cl = str(c)
        if cl in ("收盘", "close", "Close"):
            colmap[c] = "close"
        elif cl in ("开盘", "open", "Open"):
            colmap[c] = "open"
        elif cl in ("最高", "high", "High"):
            colmap[c] = "high"
        elif cl in ("最低", "low", "Low"):
            colmap[c] = "low"
        elif cl in ("成交量", "volume", "Volume"):
            colmap[c] = "volume"
        elif cl in ("日期", "date", "Date"):
            colmap[c] = "date"
    df = df.rename(columns=colmap)
    if "close" not in df.columns:
        return {"error": f"列缺失: {list(df.columns)}"}

    close = df["close"].astype(float)
    high = df["high"].astype(float) if "high" in df.columns else close
    low = df["low"].astype(float) if "low" in df.columns else close

    out: Dict[str, Any] = {
        "last_close": _f(close.iloc[-1]),
        "ma5": _f(close.tail(5).mean()) if len(close) >= 5 else None,
        "ma20": _f(close.tail(20).mean()) if len(close) >= 20 else None,
        "ma60": _f(close.tail(60).mean()) if len(close) >= 60 else None,
        "ret_5d": _f(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else None,
        "ret_20d": _f(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else None,
        "volatility_20d": (
            _f(close.pct_change().tail(20).std() * (252 ** 0.5)) if len(close) >= 21 else None
        ),
    }
    if out["ma20"]:
        out["bias_ma20"] = _f(out["last_close"] / out["ma20"] - 1)

    # MACD
    macd_df = _macd(close)
    out["macd"] = {
        "dif": _f(macd_df["dif"].iloc[-1]),
        "dea": _f(macd_df["dea"].iloc[-1]),
        "hist": _f(macd_df["macd_hist"].iloc[-1]),
        "hist_prev": _f(macd_df["macd_hist"].iloc[-2]) if len(macd_df) >= 2 else None,
    }
    h, hp = out["macd"]["hist"], out["macd"]["hist_prev"]
    if h is not None and hp is not None:
        if hp < 0 <= h:
            out["macd"]["signal"] = "金叉偏多"
        elif hp > 0 >= h:
            out["macd"]["signal"] = "死叉偏空"
        elif h > 0:
            out["macd"]["signal"] = "柱为正（偏多动能）"
        else:
            out["macd"]["signal"] = "柱为负（偏空动能）"
    else:
        out["macd"]["signal"] = "数据不足"

    # RSI
    rsi = _rsi(close, 14, method="china")
    rsi_v = _f(rsi.iloc[-1])
    out["rsi14"] = rsi_v
    if rsi_v is None:
        out["rsi14_zone"] = "数据不足"
    elif rsi_v >= 70:
        out["rsi14_zone"] = "超买区"
    elif rsi_v <= 30:
        out["rsi14_zone"] = "超卖区"
    else:
        out["rsi14_zone"] = "中性区"

    # Bollinger
    boll = _boll(close)
    mid = _f(boll["boll_mid"].iloc[-1])
    upper = _f(boll["boll_upper"].iloc[-1])
    lower = _f(boll["boll_lower"].iloc[-1])
    out["boll"] = {"mid": mid, "upper": upper, "lower": lower}
    if None not in (out["last_close"], upper, lower, mid) and upper != lower:
        pos = (out["last_close"] - lower) / (upper - lower)
        out["boll"]["position"] = _f(pos)
        if out["last_close"] >= upper:
            out["boll"]["zone"] = "触及/突破上轨"
        elif out["last_close"] <= lower:
            out["boll"]["zone"] = "触及/跌破下轨"
        else:
            out["boll"]["zone"] = "轨道内"

    # ATR
    atr = _atr(high, low, close)
    atr_v = _f(atr.iloc[-1])
    out["atr14"] = atr_v
    if atr_v and out["last_close"]:
        out["atr14_pct"] = _f(atr_v / out["last_close"])

    # KDJ
    kdj = _kdj(high, low, close)
    out["kdj"] = {
        "k": _f(kdj["kdj_k"].iloc[-1]),
        "d": _f(kdj["kdj_d"].iloc[-1]),
        "j": _f(kdj["kdj_j"].iloc[-1]),
    }

    # 均线结构简评
    ma5, ma20, ma60 = out.get("ma5"), out.get("ma20"), out.get("ma60")
    if None not in (ma5, ma20, ma60):
        if ma5 > ma20 > ma60:
            out["ma_structure"] = "多头排列"
        elif ma5 < ma20 < ma60:
            out["ma_structure"] = "空头排列"
        else:
            out["ma_structure"] = "均线纠缠/过渡"
    else:
        out["ma_structure"] = "数据不足"

    return out
