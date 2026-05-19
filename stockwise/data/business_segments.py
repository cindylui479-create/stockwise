"""主营业务构成 / 关联企业概览（v0.13 #59）。

数据源：akshare stock_zygc_em（东财主营构成），含按产品/地区/行业分类的营收/毛利率。

用途：
  - 识别单一业务依赖（"宁德时代 95% 营收来自动力电池"= 单点风险）
  - 识别地域集中度（"伊利 85% 营收来自国内"= 出海难度大）
  - 高毛利产品 vs 低毛利产品的结构性差异
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SegmentRow:
    classification: str       # "按行业分类" / "按产品分类" / "按地区分类"
    name: str                 # 具体细项名
    revenue: Optional[float] = None
    revenue_pct: Optional[float] = None
    profit: Optional[float] = None
    profit_pct: Optional[float] = None
    gross_margin: Optional[float] = None  # 0-1


@dataclass
class BusinessSegments:
    latest_period: Optional[str] = None
    by_product: list[SegmentRow] = field(default_factory=list)
    by_region: list[SegmentRow] = field(default_factory=list)
    by_industry: list[SegmentRow] = field(default_factory=list)
    skipped: bool = False
    error: Optional[str] = None


def fetch_segments(code: str, market: str = "A") -> BusinessSegments:
    """拉取最近一期主营构成。仅 A 股支持。"""
    out = BusinessSegments()
    if market != "A":
        out.skipped = True
        out.error = "主营构成暂不支持港股"
        return out

    try:
        import akshare as ak
        from stockwise.data.cache import cached_call, TTL_FINANCIALS
        # 东财格式 SH600519 / SZ000858
        prefix = "SH" if code.startswith("6") else "SZ"
        symbol = f"{prefix}{code}"
        df = cached_call(
            "em:stock_zygc", symbol, TTL_FINANCIALS,
            lambda: ak.stock_zygc_em(symbol=symbol),
        )
    except Exception as e:
        out.error = f"主营构成接口失败：{type(e).__name__}: {e}"
        return out

    if df is None or df.empty:
        return out

    # 取最新报告期
    latest = df.iloc[0]["报告日期"]
    out.latest_period = str(latest)[:10]
    latest_df = df[df["报告日期"] == latest]

    for _, row in latest_df.iterrows():
        cls = str(row.get("分类类型", "")).strip()
        seg = SegmentRow(
            classification=cls,
            name=str(row.get("主营构成", "")).strip(),
            revenue=_to_float(row.get("主营收入")),
            revenue_pct=_to_float(row.get("收入比例")),
            profit=_to_float(row.get("主营利润")),
            profit_pct=_to_float(row.get("利润比例")),
            gross_margin=_to_float(row.get("毛利率")),
        )
        # 过滤"其他(补充)" + 占比 < 1% 的细项
        if "其他(补充)" in seg.name or (seg.revenue_pct and abs(seg.revenue_pct) < 0.01):
            continue
        if cls == "按产品分类":
            out.by_product.append(seg)
        elif cls == "按地区分类":
            out.by_region.append(seg)
        elif cls == "按行业分类":
            out.by_industry.append(seg)
    # 按收入占比降序
    for lst in (out.by_product, out.by_region, out.by_industry):
        lst.sort(key=lambda s: s.revenue_pct or 0, reverse=True)
    return out


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
