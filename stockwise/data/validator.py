"""跨源数据校验：用 baostock 作为副源校验 akshare 主源数据。

设计原则：
  - 选择口径相对统一的字段：ROE / 净利率 / 毛利率 / 营收（年报口径，最近 3 年）
  - akshare 主源 vs baostock 副源
  - |主-副| / |主| × 100% 作为差异百分比
  - > 10% 视为"显著差异"，需要在报告中警示用户
  - 不做"哪个对"判定 —— 用户自己看哪个更接近披露的财报原文

注意：净利润字段在 baostock 是「含少数股东」，akshare 是「归母」，口径不同，不做该字段直接对比。
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from stockwise.data.models import (
    Financials,
    ValidationDiff,
    ValidationReport,
)


def validate(code: str, market: str, fin: Financials) -> ValidationReport:
    """对 A 股做 baostock 副源校验。港股 / 数据缺失时跳过。"""
    report = ValidationReport()
    if market != "A":
        report.skipped = True
        report.error = "副源校验仅支持 A 股"
        return report
    if not fin.annual:
        report.skipped = True
        report.error = "主源无年报数据，跳过校验"
        return report

    try:
        diffs, checked = _diff_against_baostock(code, fin)
    except Exception as e:
        report.error = f"副源调用失败：{type(e).__name__}: {e}"
        return report
    report.checked_fields = checked
    report.diffs = diffs
    return report


def _diff_against_baostock(code: str, fin: Financials) -> tuple[list[ValidationDiff], int]:
    import baostock as bs
    bs_code = ("sh." if code[0] == "6" else "sz.") + code

    _login_with_retry(bs)
    try:
        diffs: list[ValidationDiff] = []
        checked = 0
        # 取主源最近 3 年报数据做对比
        for fp in fin.annual[:3]:
            year = int(fp.period[:4])
            rs = bs.query_profit_data(code=bs_code, year=year, quarter=4)
            df = rs.get_data()
            if df is None or df.empty:
                continue
            row = df.iloc[0]
            # baostock 数值是 0-1 比例（roeAvg=0.384 表示 38.4%），akshare 是百分数
            bs_roe = _to_pct(row.get("roeAvg"))
            bs_npmargin = _to_pct(row.get("npMargin"))
            bs_gpmargin = _to_pct(row.get("gpMargin"))
            bs_revenue = _to_float(row.get("MBRevenue"))

            period = f"{year}-12-31"
            checked += _maybe_add(diffs, "ROE", period, fp.roe, bs_roe)
            checked += _maybe_add(diffs, "净利率", period, fp.net_margin, bs_npmargin)
            checked += _maybe_add(diffs, "毛利率", period, fp.gross_margin, bs_gpmargin)
            checked += _maybe_add(diffs, "营收", period, fp.revenue, bs_revenue)
        return diffs, checked
    finally:
        bs.logout()


def _maybe_add(diffs: list[ValidationDiff], fld: str, period: str,
                primary: Optional[float], secondary: Optional[float]) -> int:
    if primary is None or secondary is None:
        return 0
    if abs(primary) < 1e-6:
        return 0
    pct = abs(primary - secondary) / abs(primary) * 100
    # 只保留显著差异（>10%）；轻微差异（< 10%）不进 diffs 但计入 checked
    if pct > 10:
        diffs.append(ValidationDiff(
            field=fld, period=period,
            primary=float(primary), secondary=float(secondary),
            pct_diff=pct,
        ))
    return 1


def _login_with_retry(bs, attempts: int = 3, delay: float = 2.0) -> None:
    """baostock 偶发的「网络接收错误」做重试。"""
    last_msg = ""
    for i in range(attempts):
        lg = bs.login()
        if lg.error_code == "0":
            return
        last_msg = lg.error_msg or "未知错误"
        if i < attempts - 1:
            time.sleep(delay * (i + 1))
    raise RuntimeError(f"baostock login 重试 {attempts} 次仍失败：{last_msg}")


def _to_pct(v) -> Optional[float]:
    """baostock 比例 0-1 → 百分数 0-100。"""
    f = _to_float(v)
    return f * 100 if f is not None else None


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None
