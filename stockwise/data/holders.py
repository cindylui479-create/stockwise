"""股东结构 & 持仓变动抓取。

A 股：akshare `stock_gdfx_free_top_10_em` —— 十大流通股东 + 季度增减
港股：yfinance `major_holders` —— 内部人持股 / 机构持股总览

A 股识别这些特殊角色作为「专业投资者信号」：
  - 香港中央结算 = 北上资金（外资视角）
  - 「证券投资基金」 = 公募基金动作
  - 名称含「QFII」「保险」「社保」「养老」 = 长线资金
  - 控股股东自身增减
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import akshare as ak

from stockwise.data.models import HolderInfo, HolderRecord


def fetch_holders(code: str, market: str, yf_info: Optional[dict] = None) -> HolderInfo:
    if market == "A":
        return _fetch_a_holders(code)
    return _fetch_hk_holders(yf_info)


def _fetch_a_holders(code: str) -> HolderInfo:
    from stockwise.data.cache import cached_call, TTL_HOLDERS
    sym = ("sh" if code[0] == "6" else "sz") + code
    candidates = _recent_quarter_ends()
    for date in candidates:
        try:
            df = cached_call(
                "akshare:stock_gdfx_free_top_10_em", f"{sym}:{date}", TTL_HOLDERS,
                lambda: ak.stock_gdfx_free_top_10_em(symbol=sym, date=date),
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        top: list[HolderRecord] = []
        for _, row in df.head(10).iterrows():
            change = str(row.get("增减", "")).strip() or None
            change_pct = _to_float(row.get("变动比率"))
            # NaN 检测：_to_float 已处理 NaN→None；这里再防一手
            if change_pct is not None and (change_pct != change_pct):  # NaN check
                change_pct = None
            top.append(HolderRecord(
                name=str(row.get("股东名称", "")).strip(),
                nature=str(row.get("股东性质", "")).strip() or None,
                pct=_to_float(row.get("占总流通股本持股比例")),
                change=change,
                change_pct=change_pct,
            ))
        return HolderInfo(
            source="akshare (十大流通股东)",
            report_date=date,
            top_holders=top,
        )
    return HolderInfo(error="所有最近 4 个季度都拉取失败")


def _fetch_hk_holders(yf_info: Optional[dict]) -> HolderInfo:
    """港股用 yfinance major_holders。注意 yfinance 机构数据偏美股 13F，
    对港股本地基金覆盖弱，仅作总览参考。"""
    if not yf_info:
        return HolderInfo(skipped=True, error="无 yfinance 数据")
    return HolderInfo(
        source="yfinance major_holders",
        insider_pct=_pct(yf_info.get("heldPercentInsiders")),
        institution_pct=_pct(yf_info.get("heldPercentInstitutions")),
        institution_count=_int(yf_info.get("institutionsCount")),
    )


def _recent_quarter_ends() -> list[str]:
    """最近 4 个季度末日期，从最近开始。"""
    today = datetime.today()
    ends = []
    for q_offset in range(4):
        y = today.year
        m = today.month - q_offset * 3
        while m <= 0:
            m += 12
            y -= 1
        # 季度末月份
        q_month = ((m - 1) // 3 + 1) * 3
        last_day = {3: 31, 6: 30, 9: 30, 12: 31}[q_month]
        ends.append(f"{y}{q_month:02d}{last_day}")
    return ends


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _pct(v) -> Optional[float]:
    f = _to_float(v)
    return f * 100 if f is not None else None


def _int(v) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None
