"""profile 路由测试：classify_industry_view 在各行业 + 财务数据组合下走对档。"""
from __future__ import annotations

from stockwise.data.fetcher import classify_industry_view
from stockwise.data.models import Financials, FinancialPeriod


def _fin(revenues: list[float], gross_margins: list[float] | None = None) -> Financials:
    """构造 Financials，revenues 按 [最新, ..., 最旧] 顺序。"""
    if gross_margins is None:
        gross_margins = [35.0] * len(revenues)
    return Financials(annual=[
        FinancialPeriod(
            period=f"{2025 - i}1231",
            revenue=r,
            gross_margin=gm,
        )
        for i, (r, gm) in enumerate(zip(revenues, gross_margins))
    ])


# ---- 金融 / 周期 / 成长 ----

def test_bank_routing():
    assert classify_industry_view("货币金融服务", None) == "bank"
    assert classify_industry_view("银行业", None) == "bank"


def test_insurance_routing():
    assert classify_industry_view("保险业", None) == "insurance"


def test_cyclical_routing():
    assert classify_industry_view("煤炭开采", None) == "cyclical"
    assert classify_industry_view("钢铁", None) == "cyclical"


def test_growth_industry_high_cagr():
    # 5 年 CAGR > 15%（1e9 → 2e9 ≈ 19% CAGR）
    fin = _fin([2.0e9, 1.7e9, 1.4e9, 1.2e9, 1.0e9])
    assert classify_industry_view("计算机应用", fin) == "growth"


def test_growth_pure_finance():
    # CAGR ≥ 25%（1e9 → 3.5e9 ≈ 36% CAGR）
    fin = _fin([3.5e9, 2.5e9, 1.8e9, 1.4e9, 1.0e9])
    assert classify_industry_view("休闲服务", fin) == "growth"


# ---- 半成长 ----

def test_semi_growth_cagr_12_15():
    # CAGR ≈ 13%（1e9 → 1.65e9 over 4 years 约 13.34%）+ 毛利 35%
    fin = _fin([1.65e9, 1.45e9, 1.28e9, 1.13e9, 1.0e9], [35.0] * 5)
    assert classify_industry_view("食品", fin) == "semi_growth"


def test_semi_growth_gross_margin_too_low():
    # CAGR 13% 但毛利只有 20% → 不达半成长门槛 → default
    fin = _fin([1.65e9, 1.45e9, 1.28e9, 1.13e9, 1.0e9], [20.0] * 5)
    assert classify_industry_view("制造业", fin) == "default"


def test_semi_growth_excludes_cyclical_industry():
    # 房地产 CAGR 13% 毛利 35% 也走 default（房地产在 _NON_GROWTH_KEYS 中）
    fin = _fin([1.65e9, 1.45e9, 1.28e9, 1.13e9, 1.0e9], [35.0] * 5)
    assert classify_industry_view("房地产开发", fin) == "default"


def test_semi_growth_excludes_bank():
    # 银行业即使 CAGR 13% 毛利 35% 也走 bank（bank 路由优先级最高）
    fin = _fin([1.65e9, 1.45e9, 1.28e9, 1.13e9, 1.0e9], [35.0] * 5)
    assert classify_industry_view("货币金融服务", fin) == "bank"


def test_default_fallback():
    # 普通制造业 CAGR 6%
    fin = _fin([1.27e9, 1.2e9, 1.13e9, 1.07e9, 1.0e9], [25.0] * 5)
    assert classify_industry_view("机械制造", fin) == "default"


def test_no_financials_default():
    """无财务数据时默认走 default。"""
    assert classify_industry_view("普通制造", None) == "default"
