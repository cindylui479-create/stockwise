"""伯克希尔范式打分（按行业分发）。

行业类型（通过 INDUSTRYCSRC1 字段识别）：
  - default:   消费 / 制造 / 科技等一般企业（看 ROE / 毛利 / FCF / DCF）
  - bank:      银行业（看 ROA / ROE / P/B / 隐含回报 / 股息率 / 留存复利）
  - insurance: 保险业（看 ROE / P/B / 股息率 / 净利稳定性）

7 个维度（总分 100）：
  护城河 25 + 盈利质量 20 + 资本配置 15 + 长期增长 5 + 安全边际 20 + 业务可理解 10 + 管理层 5

v0.7 调整：能力圈权重从 5 → 10（巴菲特"看不懂的不买"），长期增长从 10 → 5
（增长本身不构成好生意，护城河 25 已覆盖；价值投资者更怕"看不懂"）。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from stockwise.analyzer.sell_signals import SellSignal, analyze as analyze_sell_signals, degrade_action
from stockwise.data.fetcher import classify_industry_view
from stockwise.data.models import (
    DividendInfo,
    Financials,
    IntrinsicValue,
    StockSnapshot,
    Valuation,
)


@dataclass
class ScoreResult:
    total: int
    rating: str                       # 质量评级
    action: str = "观察"              # 行动建议（4 档 + 否决）
    margin_of_safety: str = "未知"
    industry_view: str = "default"
    dimensions: dict[str, int] = field(default_factory=dict)
    dimension_caps: dict[str, int] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    vetoes: list[str] = field(default_factory=list)
    checklist: list[tuple[str, bool, str]] = field(default_factory=list)
    sell_signals: list[SellSignal] = field(default_factory=list)


DIMENSION_CAPS = {
    "护城河": 25,
    "盈利质量": 20,
    "资本配置": 15,
    "长期增长": 5,
    "安全边际": 20,
    "业务可理解性": 10,
    "管理层质量": 5,
}


def score(snapshot: StockSnapshot,
          llm_business_score: Optional[int] = None,
          llm_management_score: Optional[int] = None) -> ScoreResult:
    fin = snapshot.financials
    val = snapshot.valuation
    iv = snapshot.intrinsic
    div = snapshot.dividends
    view = classify_industry_view(snapshot.profile.industry, snapshot.financials)

    vetoes = _vetoes(fin, val, view=view, industry=snapshot.profile.industry or "")

    moat_pts, moat_flags, moat_reasons, cl_a = _score_moat(fin, view)
    quality_pts, quality_flags, cl_b = _score_quality(fin, view)
    capital_pts, capital_flags, cl_c = _score_capital(fin, div, view)
    growth_pts, growth_flags, growth_reasons, cl_d = _score_growth(fin, view)
    safety_pts, safety_flags, safety_reasons, cl_e = _score_safety(iv)
    biz_pts, biz_note = _score_business(llm_business_score)
    mgmt_pts, mgmt_note = _score_management(llm_management_score)

    dims = {
        "护城河": moat_pts,
        "盈利质量": quality_pts,
        "资本配置": capital_pts,
        "长期增长": growth_pts,
        "安全边际": safety_pts,
        "业务可理解性": biz_pts,
        "管理层质量": mgmt_pts,
    }
    total = sum(dims.values())
    rating = _rating(total, iv.discount, vetoes)
    sell_signals = analyze_sell_signals(snapshot)
    action = _action(total, iv.discount, vetoes, snapshot.governance,
                     llm_business_score=llm_business_score,
                     sell_signals=sell_signals)

    flags = [*moat_flags, *quality_flags, *capital_flags, *growth_flags, *safety_flags]
    if biz_note:
        flags.append(biz_note)
    if mgmt_note:
        flags.append(mgmt_note)
    reasons = [*moat_reasons, *growth_reasons, *safety_reasons]
    checklist = [*cl_a, *cl_b, *cl_c, *cl_d, *cl_e]

    return ScoreResult(
        total=total,
        rating=rating,
        action=action,
        margin_of_safety=iv.margin_of_safety,
        industry_view=view,
        dimensions=dims,
        dimension_caps=dict(DIMENSION_CAPS),
        flags=flags,
        reasons=reasons,
        vetoes=vetoes,
        checklist=checklist,
        sell_signals=sell_signals,
    )


# ---------------------------------------------------------------------------
# 一票否决
# ---------------------------------------------------------------------------

def _leverage_veto_threshold(industry: str) -> int:
    """按行业设定负债率 veto 阈值：
      - 证券 / 资本市场服务：80%（自营+保证金负债天然高）
      - 汽车 / 航空运输 / 建筑：75%（产业链占款+大额借款）
      - 其他：70%（巴菲特经典门槛）
    """
    if not industry:
        return 70
    if any(k in industry for k in ("证券", "资本市场服务")):
        return 80
    if any(k in industry for k in ("汽车", "航空运输", "建筑")):
        return 75
    return 70


def _vetoes(fin: Financials, val: Valuation, view: str = "default",
            industry: str = "") -> list[str]:
    out: list[str] = []
    if not fin.annual:
        return out
    last5 = fin.annual[:5]
    is_financial = view in ("bank", "insurance")

    # 1. 任一年净利润为负
    losses = [p for p in last5 if p.net_profit is not None and p.net_profit < 0]
    if losses:
        years = ", ".join(p.period[:4] for p in losses)
        out.append(f"近 5 年存在亏损（{years}）")

    # 2. 商誉/净资产 > 50%
    latest = fin.annual[0]
    if latest.goodwill and latest.net_profit and latest.roe and latest.roe > 0:
        equity_est = latest.net_profit / (latest.roe / 100)
        if equity_est > 0:
            gw_to_eq = latest.goodwill / equity_est
            if gw_to_eq > 0.50:
                out.append(f"商誉/净资产估算 {gw_to_eq*100:.0f}%（>50%，并购泡沫风险）")

    # 3. 负债率 veto —— 金融业完全豁免；证券 80%；汽车/航空/建筑 75%；其他 70%
    if latest.debt_ratio is not None and not is_financial:
        threshold = _leverage_veto_threshold(industry)
        if latest.debt_ratio > threshold:
            out.append(f"资产负债率 {latest.debt_ratio:.1f}%（>{threshold}%）")

    # 4. CFO 连续 2 年负 —— 金融业豁免
    if not is_financial:
        cfs = [p.operating_cashflow for p in last5[:2] if p.operating_cashflow is not None]
        if len(cfs) == 2 and all(c < 0 for c in cfs):
            out.append("经营性现金流连续 2 年为负")

    return out


# ---------------------------------------------------------------------------
# A. 护城河 (25)
# ---------------------------------------------------------------------------

def _score_moat(fin: Financials, view: str = "default"):
    if view == "bank":
        return _score_moat_bank(fin)
    if view == "insurance":
        return _score_moat_insurance(fin)
    if view == "growth":
        return _score_moat_growth(fin)
    if view == "semi_growth":
        return _score_moat_semi_growth(fin)
    return _score_moat_default(fin)


def _score_moat_semi_growth(fin: Financials):
    """半成长护城河（v0.8）：default 严格门槛对中等成长企业过严。

    ROE ≥ 12% 给 10 分（default 15%）；毛利 ≥ 30% 给 6 分（default 40%）；
    其余保留 default 的稳定性子项。
    """
    flags: list[str] = []
    reasons: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if not fin.annual:
        return 0, ["财务数据缺失"], [], []
    last5 = fin.annual[:5]
    pts = 0

    roes = [p.roe for p in last5 if p.roe is not None]
    if roes:
        roe_avg = sum(roes) / len(roes)
        if roe_avg >= 12:
            pts += 10
            reasons.append(f"近 5 年平均 ROE {roe_avg:.1f}%（半成长门槛 12%）")
            checklist.append(("ROE 5 年均值 ≥ 12%（半成长门槛）", True, f"{roe_avg:.1f}%"))
        elif roe_avg >= 9:
            pts += 5
            checklist.append(("ROE 5 年均值 ≥ 12%", False, f"{roe_avg:.1f}%"))
        else:
            checklist.append(("ROE 5 年均值 ≥ 12%", False, f"{roe_avg:.1f}%"))
        if all(r >= 10 for r in roes):
            pts += 5
            checklist.append(("每年 ROE 都 ≥ 10%", True, f"近 5 年最低 {min(roes):.1f}%"))
        else:
            checklist.append(("每年 ROE 都 ≥ 10%", False, f"近 5 年最低 {min(roes):.1f}%"))

    gms = [p.gross_margin for p in last5 if p.gross_margin is not None]
    if gms:
        gm_avg = sum(gms) / len(gms)
        if gm_avg >= 30:
            pts += 6
            checklist.append(("毛利率 5 年均值 ≥ 30%（半成长门槛）", True, f"{gm_avg:.1f}%"))
        elif gm_avg >= 20:
            pts += 3
            checklist.append(("毛利率 5 年均值 ≥ 30%", False, f"{gm_avg:.1f}%"))
        else:
            checklist.append(("毛利率 5 年均值 ≥ 30%", False, f"{gm_avg:.1f}%"))
        import statistics as _st
        if len(gms) >= 3 and gm_avg > 0:
            cv = _st.pstdev(gms) / gm_avg
            if cv < 0.15:
                pts += 4
                checklist.append(("毛利率波动率 < 15%", True, f"std/mean = {cv*100:.1f}%"))
            else:
                checklist.append(("毛利率波动率 < 15%", False, f"std/mean = {cv*100:.1f}%"))

    return min(pts, 25), flags, reasons, checklist


def _score_moat_growth(fin: Financials):
    """成长股护城河：高 CAGR + 高毛利 + 合理 ROE。"""
    flags: list[str] = []
    reasons: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if not fin.annual:
        return 0, ["财务数据缺失"], [], []
    last5 = fin.annual[:5]
    pts = 0

    # 营收 5 年 CAGR ≥ 20% (8 分；15-20 给 4 分)
    rev_series = [p.revenue for p in last5 if p.revenue is not None]
    rev_cagr = _cagr(rev_series)
    if rev_cagr is not None:
        if rev_cagr >= 20:
            pts += 8
            reasons.append(f"近 5 年营收 CAGR {rev_cagr:.1f}%（高速增长）")
            checklist.append(("营收 5 年 CAGR ≥ 20%（成长门槛）", True, f"{rev_cagr:.1f}%"))
        elif rev_cagr >= 15:
            pts += 4
            checklist.append(("营收 5 年 CAGR ≥ 20%", False, f"{rev_cagr:.1f}%（15-20 区间）"))
        else:
            checklist.append(("营收 5 年 CAGR ≥ 20%", False, f"{rev_cagr:.1f}%（已不算成长）"))

    # 毛利率 ≥ 40% 给 8 分（成长股需要定价权来 fund R&D）
    gms = [p.gross_margin for p in last5 if p.gross_margin is not None]
    if gms:
        gm_avg = sum(gms) / len(gms)
        if gm_avg >= 40:
            pts += 8
            checklist.append(("毛利率 5 年均值 ≥ 40%（成长股需定价权）", True, f"{gm_avg:.1f}%"))
        elif gm_avg >= 25:
            pts += 4
            checklist.append(("毛利率 5 年均值 ≥ 40%", False, f"{gm_avg:.1f}%"))
        else:
            checklist.append(("毛利率 5 年均值 ≥ 40%", False, f"{gm_avg:.1f}%"))

    # ROE 5 年均值 ≥ 12%（放宽，因为再投资可能稀释 ROE）
    roes = [p.roe for p in last5 if p.roe is not None]
    if roes:
        roe_avg = sum(roes) / len(roes)
        if roe_avg >= 12:
            pts += 9
            reasons.append(f"近 5 年平均 ROE {roe_avg:.1f}%")
            checklist.append(("ROE 5 年均值 ≥ 12%（成长股放宽）", True, f"{roe_avg:.1f}%"))
        elif roe_avg >= 8:
            pts += 4
            checklist.append(("ROE 5 年均值 ≥ 12%", False, f"{roe_avg:.1f}%"))
        else:
            checklist.append(("ROE 5 年均值 ≥ 12%", False, f"{roe_avg:.1f}%"))
            flags.append(f"ROE 偏低 ({roe_avg:.1f}%)，再投资效率欠佳")
    return min(pts, 25), flags, reasons, checklist


def _score_moat_default(fin: Financials):
    flags: list[str] = []
    reasons: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if not fin.annual:
        return 0, ["财务数据缺失"], [], []
    last5 = fin.annual[:5]
    pts = 0

    # ROE 5 年均值 ≥ 15%（10 分）+ 每年 ≥12%（5 分）
    roes = [p.roe for p in last5 if p.roe is not None]
    if roes:
        roe_avg = sum(roes) / len(roes)
        if roe_avg >= 15:
            pts += 10
            reasons.append(f"近 5 年平均 ROE {roe_avg:.1f}%，盈利能力强")
            checklist.append(("ROE 5 年均值 ≥ 15%", True, f"{roe_avg:.1f}%"))
        elif roe_avg >= 10:
            pts += 6
            checklist.append(("ROE 5 年均值 ≥ 15%", False, f"{roe_avg:.1f}%"))
        else:
            checklist.append(("ROE 5 年均值 ≥ 15%", False, f"{roe_avg:.1f}%"))
            flags.append(f"ROE 偏低 ({roe_avg:.1f}%)")
        if all(r >= 12 for r in roes):
            pts += 5
            checklist.append(("每年 ROE 都 ≥ 12%", True, f"近 5 年最低 {min(roes):.1f}%"))
        else:
            checklist.append(("每年 ROE 都 ≥ 12%", False, f"近 5 年最低 {min(roes):.1f}%"))
    else:
        checklist.append(("ROE 5 年均值 ≥ 15%", False, "数据缺失"))

    # 毛利率 ≥ 40% (6) + 毛利稳定性 < 15% (4)
    gms = [p.gross_margin for p in last5 if p.gross_margin is not None]
    if gms:
        gm_avg = sum(gms) / len(gms)
        if gm_avg >= 40:
            pts += 6
            checklist.append(("毛利率 5 年均值 ≥ 40%", True, f"{gm_avg:.1f}%"))
        elif gm_avg >= 30:
            pts += 3
            checklist.append(("毛利率 5 年均值 ≥ 40%", False, f"{gm_avg:.1f}%"))
        else:
            checklist.append(("毛利率 5 年均值 ≥ 40%", False, f"{gm_avg:.1f}%"))
        if len(gms) >= 3 and gm_avg > 0:
            cv = statistics.pstdev(gms) / gm_avg
            if cv < 0.15:
                pts += 4
                checklist.append(("毛利率波动率 < 15%", True, f"std/mean = {cv*100:.1f}%"))
            else:
                checklist.append(("毛利率波动率 < 15%", False, f"std/mean = {cv*100:.1f}%"))
                if cv > 0.30:
                    flags.append(f"毛利率波动较大（{cv*100:.0f}%）")
    return min(pts, 25), flags, reasons, checklist


def _score_moat_bank(fin: Financials):
    """银行护城河：ROA 1% 是巴菲特门槛，ROE 13% 优质，净利率高且稳定。

    （没有 ROA 字段时用 ROE / 杠杆估算：ROA ≈ ROE × (1-debt_ratio/100)）
    """
    flags: list[str] = []
    reasons: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if not fin.annual:
        return 0, ["财务数据缺失"], [], []
    last5 = fin.annual[:5]
    pts = 0

    # ROA 估算（10 分）
    roas: list[float] = []
    for p in last5:
        if p.roe is not None and p.debt_ratio is not None:
            equity_ratio = (100 - p.debt_ratio) / 100
            roa = p.roe * equity_ratio
            roas.append(roa)
    if roas:
        roa_avg = sum(roas) / len(roas)
        if roa_avg >= 1.0:
            pts += 10
            reasons.append(f"近 5 年平均 ROA {roa_avg:.2f}%（巴菲特银行门槛 1%）")
            checklist.append(("ROA 5 年均值 ≥ 1%（巴菲特银行门槛）", True, f"{roa_avg:.2f}%"))
        elif roa_avg >= 0.8:
            pts += 6
            checklist.append(("ROA 5 年均值 ≥ 1%", False, f"{roa_avg:.2f}%"))
        else:
            checklist.append(("ROA 5 年均值 ≥ 1%", False, f"{roa_avg:.2f}%"))
            flags.append(f"ROA 仅 {roa_avg:.2f}%，远低于 1% 门槛")

    # ROE 5 年均值 ≥ 13%（8 分）
    roes = [p.roe for p in last5 if p.roe is not None]
    if roes:
        roe_avg = sum(roes) / len(roes)
        if roe_avg >= 13:
            pts += 8
            reasons.append(f"近 5 年平均 ROE {roe_avg:.1f}%")
            checklist.append(("ROE 5 年均值 ≥ 13%", True, f"{roe_avg:.1f}%"))
        elif roe_avg >= 10:
            pts += 4
            checklist.append(("ROE 5 年均值 ≥ 13%", False, f"{roe_avg:.1f}%"))
        else:
            checklist.append(("ROE 5 年均值 ≥ 13%", False, f"{roe_avg:.1f}%"))

    # ROE 稳定性 std/mean < 20%（7 分）
    if len(roes) >= 3:
        roe_avg = sum(roes) / len(roes)
        if roe_avg > 0:
            cv = statistics.pstdev(roes) / roe_avg
            if cv < 0.20:
                pts += 7
                checklist.append(("ROE 波动率 < 20%", True, f"std/mean = {cv*100:.1f}%"))
            else:
                checklist.append(("ROE 波动率 < 20%", False, f"std/mean = {cv*100:.1f}%"))

    return min(pts, 25), flags, reasons, checklist


def _score_moat_insurance(fin: Financials):
    """保险护城河：ROE 12% 优质 + 净利率 8% + 稳定性。"""
    flags: list[str] = []
    reasons: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if not fin.annual:
        return 0, ["财务数据缺失"], [], []
    last5 = fin.annual[:5]
    pts = 0

    roes = [p.roe for p in last5 if p.roe is not None]
    if roes:
        roe_avg = sum(roes) / len(roes)
        if roe_avg >= 12:
            pts += 10
            reasons.append(f"近 5 年平均 ROE {roe_avg:.1f}%")
            checklist.append(("ROE 5 年均值 ≥ 12%（保险业门槛）", True, f"{roe_avg:.1f}%"))
        elif roe_avg >= 9:
            pts += 5
            checklist.append(("ROE 5 年均值 ≥ 12%", False, f"{roe_avg:.1f}%"))
        else:
            checklist.append(("ROE 5 年均值 ≥ 12%", False, f"{roe_avg:.1f}%"))
            flags.append(f"ROE 偏低 ({roe_avg:.1f}%)，保险业护城河弱")

    nms = [p.net_margin for p in last5 if p.net_margin is not None]
    if nms:
        nm_avg = sum(nms) / len(nms)
        if nm_avg >= 8:
            pts += 8
            checklist.append(("净利率 5 年均值 ≥ 8%（保险业）", True, f"{nm_avg:.1f}%"))
        elif nm_avg >= 5:
            pts += 4
            checklist.append(("净利率 5 年均值 ≥ 8%", False, f"{nm_avg:.1f}%"))
        else:
            checklist.append(("净利率 5 年均值 ≥ 8%", False, f"{nm_avg:.1f}%"))

    # ROE 稳定性
    if len(roes) >= 3:
        roe_avg = sum(roes) / len(roes)
        if roe_avg > 0:
            cv = statistics.pstdev(roes) / roe_avg
            if cv < 0.25:
                pts += 7
                checklist.append(("ROE 波动率 < 25%", True, f"std/mean = {cv*100:.1f}%"))
            else:
                checklist.append(("ROE 波动率 < 25%", False, f"std/mean = {cv*100:.1f}%"))
    return min(pts, 25), flags, reasons, checklist


# ---------------------------------------------------------------------------
# B. 盈利质量 (20)
# ---------------------------------------------------------------------------

def _score_quality(fin: Financials, view: str = "default"):
    flags: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if not fin.annual:
        return 0, ["财务数据缺失"], []
    last5 = fin.annual[:5]
    pts = 0
    is_financial = view in ("bank", "insurance")
    is_growth = view == "growth"

    # CFO/净利 阈值：默认 0.85；成长股 0.7（再投资周期更长可接受）；金融业跳过
    cfo_threshold = 0.70 if is_growth else 0.85
    cfo_label = f"5 年累计 CFO/净利润 ≥ {cfo_threshold:.2f}"
    if is_financial:
        pts += 7
        checklist.append(("5 年累计 CFO/净利润 ≥ 0.85", False,
                          "金融业 CFO 口径不同（含存款变动等），按中位计"))
    else:
        cfo_sum = sum((p.operating_cashflow for p in last5 if p.operating_cashflow), 0.0)
        np_sum = sum((p.net_profit for p in last5 if p.net_profit), 0.0)
        if cfo_sum and np_sum and np_sum > 0:
            ratio = cfo_sum / np_sum
            if ratio >= cfo_threshold:
                pts += 10
                checklist.append((cfo_label, True, f"{ratio:.2f}"))
            elif ratio >= cfo_threshold - 0.15:
                pts += 5
                checklist.append((cfo_label, False, f"{ratio:.2f}"))
            else:
                checklist.append((cfo_label, False, f"{ratio:.2f}"))
                flags.append(f"5 年累计 CFO/净利润 仅 {ratio:.2f}")
        elif fin.annual[0].operating_cashflow is None:
            pts += 5
            checklist.append((cfo_label, False, "数据缺失，给中位"))

    # 无亏损年（5 分）
    profits = [p.net_profit for p in last5 if p.net_profit is not None]
    if profits and all(p > 0 for p in profits):
        pts += 5
        checklist.append(("近 5 年无亏损年", True, f"最低 {min(profits)/1e8:.1f} 亿"))
    elif profits:
        checklist.append(("近 5 年无亏损年", False, "存在亏损"))

    # 净利率/净利稳定性（5 分）
    # 默认体系用 net_margin 波动；金融业 net_margin 概念不同 → 用净利润绝对值波动
    if is_financial:
        if len(profits) >= 3:
            mean = sum(profits) / len(profits)
            if mean > 0:
                cv = statistics.pstdev(profits) / mean
                threshold = 0.40 if view == "insurance" else 0.30
                label = f"净利波动 < {int(threshold*100)}%"
                if cv < threshold:
                    pts += 5
                    checklist.append((label, True, f"std/mean = {cv*100:.1f}%"))
                else:
                    checklist.append((label, False, f"std/mean = {cv*100:.1f}%"))
    else:
        nms = [p.net_margin for p in last5 if p.net_margin is not None]
        if len(nms) >= 3:
            nm_avg = sum(nms) / len(nms)
            if nm_avg > 0:
                cv = statistics.pstdev(nms) / nm_avg
                if cv < 0.25:
                    pts += 5
                    checklist.append(("净利率波动 < 25%", True, f"std/mean = {cv*100:.1f}%"))
                else:
                    checklist.append(("净利率波动 < 25%", False, f"std/mean = {cv*100:.1f}%"))

    return min(pts, 20), flags, checklist


# ---------------------------------------------------------------------------
# C. 资本配置 (15)
# ---------------------------------------------------------------------------

def _score_capital(fin: Financials, div: DividendInfo, view: str = "default"):
    flags: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if not fin.annual:
        return 0, ["财务数据缺失"], []
    latest = fin.annual[0]
    pts = 0
    is_financial = view in ("bank", "insurance")
    is_growth = view == "growth"

    # 成长股专属：研发投入占比 ≥ 10%（占用 3 分）
    if is_growth:
        rd_ratios = [p.rd_ratio for p in fin.annual[:3] if p.rd_ratio is not None]
        if rd_ratios:
            rd_avg = sum(rd_ratios) / len(rd_ratios)
            if rd_avg >= 10:
                pts += 3
                checklist.append(("研发占营收 ≥ 10%（科技股核心）", True, f"{rd_avg:.1f}%"))
            elif rd_avg >= 5:
                pts += 1
                checklist.append(("研发占营收 ≥ 10%", False, f"{rd_avg:.1f}%"))
            else:
                checklist.append(("研发占营收 ≥ 10%", False, f"{rd_avg:.1f}%"))
        else:
            pts += 1
            checklist.append(("研发占营收 ≥ 10%", False,
                              "缺数据（需 TUSHARE_TOKEN）"))

    # 资产负债率 ≤ 50%（金融业天然高杠杆，跳过给中位）
    if is_financial:
        pts += 3
        debt_str = f"{latest.debt_ratio:.1f}%" if latest.debt_ratio is not None else "—"
        checklist.append(("资产负债率（金融业商业模式注释）", False,
                          f"{debt_str}（天然高杠杆，按中位计）"))
    elif latest.debt_ratio is not None:
        if latest.debt_ratio <= 50:
            pts += 5
            checklist.append(("资产负债率 ≤ 50%", True, f"{latest.debt_ratio:.1f}%"))
        elif latest.debt_ratio <= 65:
            pts += 2
            checklist.append(("资产负债率 ≤ 50%", False, f"{latest.debt_ratio:.1f}%"))
        else:
            checklist.append(("资产负债率 ≤ 50%", False, f"{latest.debt_ratio:.1f}%"))
            flags.append(f"资产负债率偏高 ({latest.debt_ratio:.1f}%)")

    # 持续分红
    if div.consecutive_years >= 5:
        pts += 6
        checklist.append(("连续派息 ≥ 5 年", True, f"已连续 {div.consecutive_years} 年"))
    elif div.consecutive_years >= 3:
        pts += 3
        checklist.append(("连续派息 ≥ 5 年", False, f"连续 {div.consecutive_years} 年"))
    elif div.history:
        checklist.append(("连续派息 ≥ 5 年", False, "分红历史不连续"))
    else:
        checklist.append(("连续派息 ≥ 5 年", False, "无分红记录或数据缺失"))

    # 商誉
    if latest.goodwill is not None and latest.net_profit and latest.net_profit > 0:
        if latest.goodwill == 0:
            pts += 4
            checklist.append(("商誉/年净利 ≤ 1 倍", True, "无商誉（内生增长，理想）"))
        else:
            gw_ratio = latest.goodwill / latest.net_profit
            if gw_ratio <= 1:
                pts += 4
                checklist.append(("商誉/年净利 ≤ 1 倍", True, f"{gw_ratio:.2f} 倍"))
            elif gw_ratio <= 3:
                pts += 2
                checklist.append(("商誉/年净利 ≤ 1 倍", False, f"{gw_ratio:.2f} 倍"))
            else:
                checklist.append(("商誉/年净利 ≤ 1 倍", False, f"{gw_ratio:.1f} 倍"))
                flags.append(f"商誉/年净利 {gw_ratio:.1f} 倍，存在减值风险")
    elif latest.goodwill is None:
        pts += 2
        checklist.append(("商誉/年净利 ≤ 1 倍", False, "数据缺失，给中位"))

    return min(pts, 15), flags, checklist


# ---------------------------------------------------------------------------
# D. 长期增长 (10)
# ---------------------------------------------------------------------------

def _score_growth(fin: Financials, view: str = "default"):
    flags: list[str] = []
    reasons: list[str] = []
    checklist: list[tuple[str, bool, str]] = []
    if len(fin.annual) < 3:
        return 0, ["财务历史不足 3 年"], [], []
    pts = 0

    # 行业差异化门槛：
    #   银行/保险    — 增速低 → 8%
    #   成长股       — 高速增长 → 25% 净利、20% 营收
    #   半成长 (v0.8)— 中等增长 → 12% 净利、8% 营收
    #   默认         — 10% 净利、5% 营收
    if view in ("bank", "insurance"):
        profit_thr, rev_thr = 8, 5
    elif view == "growth":
        profit_thr, rev_thr = 25, 20
    elif view == "semi_growth":
        profit_thr, rev_thr = 12, 8
    else:
        profit_thr, rev_thr = 10, 5

    rev_series = [p.revenue for p in fin.annual if p.revenue is not None]
    rev_cagr = _cagr(rev_series)
    if rev_cagr is not None:
        if rev_cagr >= rev_thr:
            pts += 3
            checklist.append((f"营收 {len(rev_series)-1} 年 CAGR ≥ {rev_thr}%", True, f"{rev_cagr:.1f}%"))
        else:
            checklist.append((f"营收 {len(rev_series)-1} 年 CAGR ≥ {rev_thr}%", False, f"{rev_cagr:.1f}%"))

    profit_series = [p.net_profit for p in fin.annual if p.net_profit is not None]
    profit_cagr = _cagr(profit_series)
    if profit_cagr is not None:
        if profit_cagr >= profit_thr:
            pts += 3
            reasons.append(f"近 {len(profit_series)-1} 年净利 CAGR {profit_cagr:.1f}%")
            checklist.append((f"净利 {len(profit_series)-1} 年 CAGR ≥ {profit_thr}%", True, f"{profit_cagr:.1f}%"))
        else:
            checklist.append((f"净利 {len(profit_series)-1} 年 CAGR ≥ {profit_thr}%", False, f"{profit_cagr:.1f}%"))

    if len(profit_series) >= 4:
        rolls_ok = True
        for i in range(len(profit_series) - 3):
            window = profit_series[i:i + 4]
            if not all(window[j] > window[j + 1] for j in range(3)):
                rolls_ok = False
                break
        if rolls_ok:
            pts += 4
            checklist.append(("连续 3 年净利逐年增长", True, "全部为正"))
        else:
            checklist.append(("连续 3 年净利逐年增长", False, "存在下滑年份"))
            if profit_series[0] < profit_series[1]:
                flags.append(f"最近一年净利回落（{profit_series[1]/1e8:.0f}亿 → {profit_series[0]/1e8:.0f}亿）")

    # v0.7：长期增长 cap 从 10 → 5（增长本身不构成好生意），按比例缩到 0-5
    return min((pts + 1) // 2, 5), flags, reasons, checklist


# ---------------------------------------------------------------------------
# E. 安全边际 (20)  —— 直接读 IntrinsicValue.gates，每关 5 分
# ---------------------------------------------------------------------------

def _score_safety(iv: IntrinsicValue):
    """安全边际：按 discount 连续映射 0-20 分。

    映射区间：discount ∈ [-30%, +50%] → pts ∈ [0, 20]
      discount ≥ +50%   → 20（深度低估）
      +30%               → 15
      +10%               → 10
      -10%               → 5
      -30%               → 0
    """
    flags: list[str] = []
    reasons: list[str] = []
    checklist: list[tuple[str, bool, str]] = []

    # 仍展示每道关的通过情况（信息保留）
    for gate in iv.gates:
        if gate.passed:
            checklist.append((gate.label, True, gate.current_str))
        else:
            checklist.append((gate.label, False, gate.current_str))

    if iv.discount is None:
        return 5, ["内在价值无法估算（数据缺失），给中位 5 分"], [], checklist

    d = iv.discount
    # 线性映射 [-30, 50] → [0, 20]，clamp 边界
    pts = (d + 30) / 80 * 20
    pts = max(0, min(20, round(pts)))

    if d >= 30:
        reasons.append(f"安全边际充足：当前价格相对内在价值折价 {d:.0f}%（fair ≈ {iv.fair_value/1e8:.0f} 亿）")
    elif d >= 10:
        reasons.append(f"安全边际一般：折价 {d:.0f}%（fair ≈ {iv.fair_value/1e8:.0f} 亿）")
    elif d >= -10:
        flags.append(f"安全边际不足：当前价格接近内在价值（差 {d:+.0f}%）")
    else:
        flags.append(f"估值偏贵：当前价格高出内在价值 {-d:.0f}%（fair ≈ {iv.fair_value/1e8:.0f} 亿）")

    return pts, flags, reasons, checklist


# ---------------------------------------------------------------------------
# F / G：业务可理解 + 管理层（来自 LLM）
# ---------------------------------------------------------------------------

def _score_business(score_0_5: Optional[int]) -> tuple[int, str]:
    """业务可理解性：LLM 给 0-5，本维度满分 10（v0.7 升权）。

    缺 LLM → 6/10（约 60%，与原 3/5 比率一致；但缺少能力圈判断会在 _action 处提醒）。
    """
    if score_0_5 is None:
        return 6, "业务可理解性：未启用 LLM，给中位分（能力圈判定缺失）"
    return max(0, min(10, score_0_5 * 2)), ""


def _score_management(score_0_5: Optional[int]) -> tuple[int, str]:
    if score_0_5 is None:
        return 3, "管理层质量：未启用 LLM，给中位分"
    return max(0, min(5, score_0_5)), ""


# ---------------------------------------------------------------------------
# 评级标签（伯克希尔风格）
# ---------------------------------------------------------------------------

def _action(total: int, discount: Optional[float], vetoes: list[str],
            governance, llm_business_score: Optional[int] = None,
            sell_signals: Optional[list] = None) -> str:
    """独立于质量评级的"具体行动建议"。

    决策矩阵：
      - 一票否决 → "避免，不研究"
      - 业务可理解性 = 0（LLM 判完全看不懂）→ "避免（业务不在能力圈内）"
      - 业务可理解性 ≤ 1 + total ≥ 70 → 强制降档到"观察（能力圈外）"
      - 质量 < 50 → "避免"
      - 质量 50-70 → "观察，不建议新仓"
      - 质量 ≥ 70 + 折价 ≥ 30% → "可以入场（折价充足）"
      - 质量 ≥ 70 + 折价 10-30% → "可以入场（谨慎，折价一般）"
      - 质量 ≥ 70 + 折价 -10~10% → "已持有可继续，新仓需等"
      - 质量 ≥ 70 + 折价 < -10% → "等待回调（估值偏贵）"
    若 governance 含红旗事件，所有建议追加 "警惕治理事件"
    """
    # 能力圈兜底（巴菲特：看不懂的不买）—— 在 veto 之后、其他档之前
    if vetoes:
        base = "避免（触发一票否决）"
    elif llm_business_score is not None and llm_business_score == 0:
        base = "避免（业务不在能力圈内）"
    elif llm_business_score is not None and llm_business_score <= 1 and total >= 70:
        base = "观察（业务可理解性极低，能力圈外）"
    elif total < 50:
        base = "避免（基本面不达标）"
    elif total < 70:
        base = "观察，不建议新仓"
    elif discount is None:
        base = "观察（估值数据不全）"
    elif discount >= 30:
        base = "可以入场（折价充足）"
    elif discount >= 10:
        base = "可以入场（谨慎，折价一般）"
    elif discount >= -10:
        base = "已持有可继续，新仓需等"
    else:
        base = "等待回调（估值偏贵）"

    if governance and hasattr(governance, "has_red_flags") and governance.has_red_flags:
        base = f"{base} ⚠ 留意治理红旗"

    # 卖出信号叠加：估值严重离谱 → 强制"考虑减仓"；生意/质量恶化 → 追加 ⚠
    if sell_signals:
        base = degrade_action(base, sell_signals, total, discount)
    return base


def _rating(total: int, discount: Optional[float], vetoes: list[str]) -> str:
    """5 档标签 + 否决档。

    v0.7：直接用 discount % 连续阈值驱动，避免 "不足/一般" 4 档 enum 在 +9% vs +11%
    这种边界处把评级砍掉一档（泸州老窖 82 分 + 折价 +10% 误降的根因）。

      veto                          → 避免
      ≥85 + discount ≥ 20%          → 值得长期持有
      ≥85 + discount ≥  0%          → 优质合理估值
      ≥85 + discount <  0%          → 优质但偏贵
      70-84 + discount ≥  0%        → 质量好且估值合理
      70-84 + discount <  0%        → 质量好但有瑕疵
      < 70                          → 未达伯克希尔标准
    """
    if vetoes:
        return "避免"
    d = 0.0 if discount is None else discount
    if total >= 85:
        if d >= 20:
            return "值得长期持有"
        if d >= 0:
            return "优质合理估值"
        return "优质但偏贵"
    if total >= 70:
        if d >= 0:
            return "质量好且估值合理"
        return "质量好但有瑕疵"
    return "未达伯克希尔标准"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _scale(value: Optional[float], low: float, high: float, max_points: float) -> float:
    if value is None:
        return max_points / 2
    if value <= low:
        return 0
    if value >= high:
        return max_points
    return (value - low) / (high - low) * max_points


def _cagr(values: list[Optional[float]]) -> Optional[float]:
    series = [v for v in values if v is not None]
    if len(series) < 2:
        return None
    latest, earliest = series[0], series[-1]
    if earliest <= 0 or latest <= 0:
        return None
    n = len(series) - 1
    return ((latest / earliest) ** (1 / n) - 1) * 100
