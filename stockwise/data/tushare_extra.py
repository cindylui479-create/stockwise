"""Tushare Pro 增强字段（研发投入 / 资本支出）。

仅在环境变量 TUSHARE_TOKEN 存在时启用，否则静默跳过。

主要用途：为未来的"成长股 profile c)"提供数据基础：
  - 研发占比 rd_ratio：科技股门槛通常 ≥ 10%
  - 资本支出 capex：用于精算 owner earnings = CFO - 维持性 capex

注：Tushare Pro 个人积分免费层够个人分析使用（每分钟 ~200 次调用）。
"""
from __future__ import annotations

import os
from typing import Optional

from stockwise.data.models import Financials


def enrich(code: str, market: str, fin: Financials) -> Optional[str]:
    """用 Tushare Pro 数据丰富 fin.annual。返回错误说明（None = 成功 / 跳过且无错）。

    成功填充 rd_exp / rd_ratio / capex 字段到 fin.annual 中匹配的报告期。
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return None      # 未配置 token → 静默跳过
    if market != "A":
        return "Tushare Pro 暂只接入 A 股"
    if not fin.annual:
        return None
    try:
        import tushare as ts
    except ImportError:
        return "tushare 包未安装"

    try:
        ts.set_token(token)
        pro = ts.pro_api()
    except Exception as e:
        return f"Tushare 鉴权失败：{e}"

    ts_code = f"{code}.{'SH' if code[0] == '6' else 'SZ'}"

    # 拉财务指标：研发占比
    try:
        df_ind = pro.fina_indicator(
            ts_code=ts_code,
            fields="ts_code,end_date,rd_exp,rd_exp_to_or",
        )
    except Exception as e:
        return f"fina_indicator: {type(e).__name__}: {e}"

    # 拉现金流量表：capex 用「购建固定资产、无形资产和其他长期资产支付的现金」
    try:
        df_cf = pro.cashflow(
            ts_code=ts_code, period_type="A",  # 年报
            fields="ts_code,end_date,c_pay_acq_const_fiolta",
        )
    except Exception as e:
        return f"cashflow: {type(e).__name__}: {e}"

    # 按 end_date 索引方便匹配
    ind_by_date = {row["end_date"]: row for _, row in df_ind.iterrows()} if df_ind is not None else {}
    cf_by_date = {row["end_date"]: row for _, row in df_cf.iterrows()} if df_cf is not None else {}

    for fp in fin.annual:
        date = fp.period  # YYYYMMDD
        if date in ind_by_date:
            r = ind_by_date[date]
            fp.rd_exp = _to_float(r.get("rd_exp"))
            fp.rd_ratio = _to_float(r.get("rd_exp_to_or"))
        if date in cf_by_date:
            r = cf_by_date[date]
            fp.capex = _to_float(r.get("c_pay_acq_const_fiolta"))
    return None


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None
