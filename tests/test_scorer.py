"""伯克希尔范式打分器测试，使用合成数据避开网络调用。"""
from __future__ import annotations

from stockwise.analyzer.scorer import _cagr, score
from stockwise.data.models import (
    CompanyProfile,
    DividendInfo,
    DividendRecord,
    Financials,
    FinancialPeriod,
    IntrinsicValue,
    StockSnapshot,
    ValueGate,
    Valuation,
)


def _profile(market_cap: float = 1e10) -> CompanyProfile:
    return CompanyProfile(
        code="000001", market="A", name="测试", industry="测试行业",
        total_market_cap=market_cap, float_market_cap=market_cap, listing_date="20000101",
        current_price=10.0, currency="CNY",
        shares_outstanding=market_cap / 10.0,
    )


def _excellent_financials() -> Financials:
    """近 5 年 ROE 30%+、毛利 60%、负债率 30%、现金流>净利、稳定增长。"""
    base_rev = 1e9
    base_profit = 3e8
    return Financials(annual=[
        FinancialPeriod(
            period=f"{2025 - i}1231",
            revenue=base_rev * (1.15 ** (4 - i)),
            net_profit=base_profit * (1.15 ** (4 - i)),
            roe=30.0,
            gross_margin=60.0,
            net_margin=30.0,
            debt_ratio=30.0,
            operating_cashflow=base_profit * (1.15 ** (4 - i)) * 1.05,
            fcf_per_share=base_profit * (1.15 ** (4 - i)) * 0.9 / 1e9,  # 每股 FCF
            goodwill=1e7,
            revenue_yoy=15.0 if i < 4 else None,
            profit_yoy=15.0 if i < 4 else None,
        )
        for i in range(5)
    ])


def _bad_financials() -> Financials:
    """ROE 3%、毛利 10%、负债率 80%、现金流为负、商誉巨大。"""
    return Financials(annual=[
        FinancialPeriod(
            period="20251231",
            revenue=1e9, net_profit=2e7, roe=3.0,
            gross_margin=10.0, net_margin=2.0, debt_ratio=80.0,
            operating_cashflow=-5e7, goodwill=1e9,
            revenue_yoy=-5.0, profit_yoy=-30.0,
        ),
        FinancialPeriod(
            period="20241231", revenue=1.05e9, net_profit=3e7, roe=4.0,
            gross_margin=11.0, debt_ratio=78.0,
            operating_cashflow=-2e7,
        ),
    ])


def _good_intrinsic() -> IntrinsicValue:
    return IntrinsicValue(
        industry_view="default",
        market_cap=1e10,
        gates=[
            ValueGate("FCF Yield ≥ 6%", "8.0%", "≥ 6%", True),
            ValueGate("Graham PE×PB ≤ 22", "15.0", "≤ 22", True),
            ValueGate("OE×12 ≥ 市值", "120 亿", "≥ 市值", True),
            ValueGate("DCF ≥ 市值", "150 亿", "≥ 市值", True),
        ],
        margin_of_safety="充足",
    )


def _bad_intrinsic() -> IntrinsicValue:
    return IntrinsicValue(
        industry_view="default",
        market_cap=1e10,
        gates=[
            ValueGate("FCF Yield ≥ 6%", "2.0%", "≥ 6%", False),
            ValueGate("Graham PE×PB ≤ 22", "120.0", "≤ 22", False),
            ValueGate("OE×12 ≥ 市值", "30 亿", "≥ 市值", False),
            ValueGate("DCF ≥ 市值", "40 亿", "≥ 市值", False),
        ],
        margin_of_safety="不足",
    )


def _good_div() -> DividendInfo:
    return DividendInfo(
        history=[DividendRecord(year=2025 - i, cash_per_10_shares=10.0) for i in range(8)],
        consecutive_years=8,
    )


def test_excellent_company_long_term_hold():
    snap = StockSnapshot(
        profile=_profile(),
        financials=_excellent_financials(),
        valuation=Valuation(pe_ttm=12.0, pb=1.5, ps=1.0, has_history=False),
        dividends=_good_div(),
        intrinsic=_good_intrinsic(),
    )
    res = score(snap, llm_business_score=5, llm_management_score=5)
    assert res.total >= 85
    assert res.rating == "值得长期持有"
    assert res.margin_of_safety == "充足"
    assert not res.vetoes


def test_quality_company_overpriced():
    """好公司但安全边际不足 → 优质但偏贵 / 质量好但有瑕疵 / 未达标准（取决于具体分）。"""
    snap = StockSnapshot(
        profile=_profile(),
        financials=_excellent_financials(),
        valuation=Valuation(pe_ttm=40.0, pb=8.0, has_history=False),
        dividends=_good_div(),
        intrinsic=_bad_intrinsic(),
    )
    res = score(snap, llm_business_score=5, llm_management_score=4)
    assert res.rating in {"优质但偏贵", "质量好但有瑕疵", "未达伯克希尔标准"}
    assert res.margin_of_safety == "不足"


def test_terrible_company_vetoed():
    snap = StockSnapshot(
        profile=_profile(),
        financials=_bad_financials(),
        valuation=Valuation(pe_ttm=80.0, pb=8.0, has_history=False),
        dividends=DividendInfo(),
        intrinsic=_bad_intrinsic(),
    )
    res = score(snap)
    # 现金流连续两年为负 + 负债率 80% 都是一票否决项
    assert res.rating == "避免"
    assert any("现金流" in v or "负债率" in v for v in res.vetoes)


def test_missing_data_neutral():
    snap = StockSnapshot(
        profile=_profile(),
        financials=Financials(),
        valuation=Valuation(),
        dividends=DividendInfo(),
        intrinsic=IntrinsicValue(),
    )
    res = score(snap)
    # 缺数据时不应崩溃；总分较低，进「未达标准」
    assert res.total < 70
    assert res.rating in {"避免", "未达伯克希尔标准"}


def test_cagr_basic():
    rate = _cagr([200, 170, 140, 120, 100])
    assert rate is not None
    assert 18 <= rate <= 19


def test_cagr_handles_negatives():
    assert _cagr([100, -50]) is None
    assert _cagr([100]) is None
    assert _cagr([]) is None
