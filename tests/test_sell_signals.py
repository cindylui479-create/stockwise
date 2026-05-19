"""卖出框架测试。"""
from __future__ import annotations

from stockwise.analyzer.sell_signals import analyze, degrade_action, SellSignal
from stockwise.data.models import (
    CompanyProfile,
    Financials,
    FinancialPeriod,
    GovernanceEvent,
    GovernanceReport,
    IntrinsicValue,
    StockSnapshot,
    Valuation,
)


def _profile() -> CompanyProfile:
    return CompanyProfile(
        code="000001", market="A", name="测试", industry="测试行业",
        total_market_cap=2.0e10, float_market_cap=2.0e10, listing_date="20000101",
        current_price=20.0, currency="CNY",
        shares_outstanding=1e9,
    )


def _iv(discount: float = 0.0) -> IntrinsicValue:
    market_cap = 2.0e10
    # discount % = (fair - cap) / fair * 100  →  fair = cap / (1 - d/100)
    fair = market_cap / (1 - discount / 100) if discount != 100 else market_cap
    return IntrinsicValue(
        industry_view="default",
        market_cap=market_cap,
        fair_value=fair,
        discount=discount,
        margin_of_safety="一般",
    )


def _snap(annual: list[FinancialPeriod], iv: IntrinsicValue = None,
          gov: GovernanceReport = None) -> StockSnapshot:
    return StockSnapshot(
        profile=_profile(),
        financials=Financials(annual=annual),
        valuation=Valuation(),
        intrinsic=iv or _iv(0.0),
        governance=gov or GovernanceReport(),
    )


def _fp(year: int, **kw) -> FinancialPeriod:
    base = dict(
        period=f"{year}1231",
        revenue=1e9, net_profit=2e8, roe=20.0,
        gross_margin=40.0, debt_ratio=40.0,
        operating_cashflow=2.2e8, goodwill=1e8,
        revenue_yoy=10.0,
    )
    base.update(kw)
    return FinancialPeriod(**base)


# ---- 生意变质 ----

def test_roe_continuous_decline_triggers_high():
    annual = [
        _fp(2025, roe=22.0),  # latest
        _fp(2024, roe=28.0),
        _fp(2023, roe=32.0),
    ]
    signals = analyze(_snap(annual))
    roe_signals = [s for s in signals if s.label == "ROE 连续衰减"]
    assert len(roe_signals) == 1
    assert roe_signals[0].severity == "high"  # 跌 10 pct >= 8
    assert roe_signals[0].category == "business"


def test_gross_margin_decline_medium():
    annual = [
        _fp(2025, gross_margin=35.0),
        _fp(2024, gross_margin=38.0),
        _fp(2023, gross_margin=41.0),  # 累计 -6 pct
    ]
    signals = analyze(_snap(annual))
    gm_signals = [s for s in signals if s.label == "毛利率连续衰减"]
    assert len(gm_signals) == 1
    assert gm_signals[0].severity == "medium"


def test_revenue_two_consecutive_negative():
    annual = [
        _fp(2025, revenue_yoy=-15.0),  # high (< -10)
        _fp(2024, revenue_yoy=-5.0),
        _fp(2023),
    ]
    signals = analyze(_snap(annual))
    rev_signals = [s for s in signals if s.label == "营收连续负增长"]
    assert len(rev_signals) == 1
    assert rev_signals[0].severity == "high"


# ---- 质量变质 ----

def test_debt_jump_triggers_quality():
    annual = [
        _fp(2025, debt_ratio=58.0),
        _fp(2024, debt_ratio=42.0),  # +16 pct → high
        _fp(2023, debt_ratio=40.0),
    ]
    signals = analyze(_snap(annual))
    debt = [s for s in signals if s.label == "负债率突增"]
    assert len(debt) == 1
    assert debt[0].severity == "high"


def test_goodwill_explosion():
    annual = [
        _fp(2025, goodwill=5e8),  # 5 倍前一年
        _fp(2024, goodwill=1e8),
        _fp(2023),
    ]
    signals = analyze(_snap(annual))
    gw = [s for s in signals if s.label == "商誉激增"]
    assert len(gw) == 1
    assert gw[0].severity == "high"


def test_governance_high_flag():
    gov = GovernanceReport(
        events=[GovernanceEvent(date="2026-01-01", category="监管立案",
                                title="证监会立案调查", severity="high")],
    )
    snap = _snap([_fp(2025), _fp(2024)], gov=gov)
    signals = analyze(snap)
    flag = [s for s in signals if s.label == "治理红旗事件"]
    assert len(flag) == 1
    assert flag[0].severity == "high"


# ---- 估值离谱 ----

def test_valuation_extreme_high():
    snap = _snap([_fp(2025), _fp(2024)], iv=_iv(discount=-55.0))
    signals = analyze(snap)
    val = [s for s in signals if s.category == "valuation"]
    assert len(val) == 1
    assert val[0].severity == "high"
    assert val[0].label == "估值严重离谱"


def test_valuation_medium():
    snap = _snap([_fp(2025), _fp(2024)], iv=_iv(discount=-35.0))
    signals = analyze(snap)
    val = [s for s in signals if s.category == "valuation"]
    assert len(val) == 1
    assert val[0].severity == "medium"


def test_valuation_safe_no_signal():
    snap = _snap([_fp(2025), _fp(2024)], iv=_iv(discount=10.0))
    signals = analyze(snap)
    val = [s for s in signals if s.category == "valuation"]
    assert len(val) == 0


def test_high_roe_relaxes_valuation_threshold():
    """ROE 5 年均 ≥ 25% 的伟大企业，discount -60% 不应该触发 high severity。"""
    annual = [_fp(2025, roe=30.0), _fp(2024, roe=29.0), _fp(2023, roe=28.0),
              _fp(2022, roe=27.0), _fp(2021, roe=26.0)]
    snap = _snap(annual, iv=_iv(discount=-60.0))
    signals = analyze(snap)
    val = [s for s in signals if s.category == "valuation"]
    assert len(val) == 1
    # ROE 高于 25%，threshold_high 放宽到 -80%，-60% 应当只到 medium
    assert val[0].severity == "medium"
    assert "高 ROE 企业" in val[0].evidence


def test_normal_roe_keeps_strict_threshold():
    """ROE 5 年均 < 25% 的普通企业，discount -55% 触发 high。"""
    annual = [_fp(2025, roe=18.0), _fp(2024, roe=17.0), _fp(2023, roe=16.0)]
    snap = _snap(annual, iv=_iv(discount=-55.0))
    signals = analyze(snap)
    val = [s for s in signals if s.category == "valuation"]
    assert len(val) == 1
    assert val[0].severity == "high"


def test_high_roe_still_triggers_high_at_extreme():
    """高 ROE 企业 discount -90% 仍触发 high（极端高估）。"""
    annual = [_fp(2025, roe=30.0), _fp(2024, roe=30.0), _fp(2023, roe=30.0)]
    snap = _snap(annual, iv=_iv(discount=-90.0))
    signals = analyze(snap)
    val = [s for s in signals if s.category == "valuation"]
    assert val[0].severity == "high"


# ---- degrade_action ----

def test_degrade_action_no_signals_passthrough():
    assert degrade_action("可以入场（折价充足）", [], 85, 30.0) == "可以入场（折价充足）"


def test_degrade_action_high_valuation_forces_sell():
    sig = [SellSignal(category="valuation", severity="high",
                      label="估值严重离谱", evidence="x")]
    out = degrade_action("等待回调（估值偏贵）", sig, 75, -55.0)
    assert "考虑减仓" in out


def test_degrade_action_high_business_appends_warning():
    sig = [SellSignal(category="business", severity="high",
                      label="ROE 连续衰减", evidence="x")]
    out = degrade_action("已持有可继续，新仓需等", sig, 75, 5.0)
    assert "⚠" in out and "生意" in out


def test_clean_company_no_signals():
    """好公司：高 ROE 稳定、低负债、无红旗、估值合理 → 无信号。"""
    annual = [_fp(2025), _fp(2024), _fp(2023)]
    signals = analyze(_snap(annual, iv=_iv(20.0)))
    assert signals == []
