"""卖出信号 / 减仓提示框架（v0.7）。

巴菲特卖出三种情形：
  ①生意变质（business deterioration）—— 护城河被侵蚀
  ②质量变质（quality deterioration）—— 财务结构 / 治理恶化
  ③估值离谱（valuation extreme）—— 高出内在价值过多

本模块独立打分体系外，不改总分，只在「行动建议」上叠加降档 +
报告新增「卖出信号」章节。

输出：每个信号带 severity（high/medium）+ category + label + evidence。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from stockwise.data.models import StockSnapshot


@dataclass
class SellSignal:
    category: str       # "business" | "quality" | "valuation"
    severity: str       # "high" | "medium"
    label: str          # 短标签（用于行动建议）
    evidence: str       # 详细说明（数字 + 上下文）


def analyze(snap: StockSnapshot) -> list[SellSignal]:
    """对 snapshot 跑全部卖出信号检查，返回触发的信号列表。"""
    signals: list[SellSignal] = []
    signals.extend(_business_deterioration(snap))
    signals.extend(_quality_deterioration(snap))
    signals.extend(_valuation_extreme(snap))
    return signals


# ---------------------------------------------------------------------------
# ① 生意变质：ROE / 毛利率 / 营收 衰减
# ---------------------------------------------------------------------------

def _business_deterioration(snap: StockSnapshot) -> list[SellSignal]:
    out: list[SellSignal] = []
    fin = snap.financials
    if len(fin.annual) < 3:
        return out

    # 取最近 3 年（含当前），最新在前
    a0, a1, a2 = fin.annual[0], fin.annual[1], fin.annual[2]

    # 1.1 ROE 连续 2 年下降，累计跌幅 ≥ 5 pct
    if a0.roe is not None and a1.roe is not None and a2.roe is not None:
        if a0.roe < a1.roe < a2.roe:
            drop = a2.roe - a0.roe
            if drop >= 5:
                out.append(SellSignal(
                    category="business",
                    severity="high" if drop >= 8 else "medium",
                    label="ROE 连续衰减",
                    evidence=(f"ROE 连续 2 年下降：{a2.period[:4]} {a2.roe:.1f}% → "
                              f"{a1.period[:4]} {a1.roe:.1f}% → "
                              f"{a0.period[:4]} {a0.roe:.1f}%，累计跌 {drop:.1f} pct"),
                ))

    # 1.2 毛利率连续 2 年下降，累计跌幅 ≥ 5 pct（产品力衰减）
    if a0.gross_margin is not None and a1.gross_margin is not None and a2.gross_margin is not None:
        if a0.gross_margin < a1.gross_margin < a2.gross_margin:
            drop = a2.gross_margin - a0.gross_margin
            if drop >= 5:
                out.append(SellSignal(
                    category="business",
                    severity="high" if drop >= 8 else "medium",
                    label="毛利率连续衰减",
                    evidence=(f"毛利率连续 2 年下降：{a2.period[:4]} {a2.gross_margin:.1f}% → "
                              f"{a1.period[:4]} {a1.gross_margin:.1f}% → "
                              f"{a0.period[:4]} {a0.gross_margin:.1f}%，累计跌 {drop:.1f} pct"
                              "（定价权 / 成本控制弱化）"),
                ))

    # 1.3 营收连续 2 年负增长（市场份额或行业空间收缩）
    if a0.revenue_yoy is not None and a1.revenue_yoy is not None:
        if a0.revenue_yoy < 0 and a1.revenue_yoy < 0:
            out.append(SellSignal(
                category="business",
                severity="high" if (a0.revenue_yoy < -10 or a1.revenue_yoy < -10) else "medium",
                label="营收连续负增长",
                evidence=(f"营收连续 2 年下滑：{a1.period[:4]} {a1.revenue_yoy:+.1f}%，"
                          f"{a0.period[:4]} {a0.revenue_yoy:+.1f}%"),
            ))

    return out


# ---------------------------------------------------------------------------
# ② 质量变质：负债 / 商誉 / 治理 / 现金流
# ---------------------------------------------------------------------------

def _quality_deterioration(snap: StockSnapshot) -> list[SellSignal]:
    out: list[SellSignal] = []
    fin = snap.financials
    gov = snap.governance

    # 2.1 负债率突增 ≥ 10 pct（加杠杆冒险）
    if len(fin.annual) >= 2:
        a0, a1 = fin.annual[0], fin.annual[1]
        if a0.debt_ratio is not None and a1.debt_ratio is not None:
            delta = a0.debt_ratio - a1.debt_ratio
            if delta >= 10:
                out.append(SellSignal(
                    category="quality",
                    severity="high" if delta >= 15 else "medium",
                    label="负债率突增",
                    evidence=(f"资产负债率 {a1.period[:4]} {a1.debt_ratio:.1f}% → "
                              f"{a0.period[:4]} {a0.debt_ratio:.1f}%（一年内 +{delta:.1f} pct，激进加杠杆）"),
                ))

    # 2.2 商誉同比突增 ≥ 50%（激进并购）
    if len(fin.annual) >= 2:
        a0, a1 = fin.annual[0], fin.annual[1]
        if a0.goodwill is not None and a1.goodwill is not None and a1.goodwill > 0:
            growth = (a0.goodwill - a1.goodwill) / a1.goodwill * 100
            if growth >= 50:
                out.append(SellSignal(
                    category="quality",
                    severity="high",
                    label="商誉激增",
                    evidence=(f"商誉 {a1.period[:4]} {a1.goodwill/1e8:.1f} 亿 → "
                              f"{a0.period[:4]} {a0.goodwill/1e8:.1f} 亿（一年内 +{growth:.0f}%，"
                              f"并购泡沫 / 未来减值风险）"),
                ))

    # 2.3 治理 high 红旗事件 ≥ 1 条
    if gov and not gov.skipped and gov.high:
        out.append(SellSignal(
            category="quality",
            severity="high",
            label="治理红旗事件",
            evidence=(f"近 180 天巨潮披露 high 级事件 {len(gov.high)} 条（监管/诉讼/失信类）：" +
                      "、".join(e.title[:30] for e in gov.high[:3])),
        ))

    # 2.4 CFO/净利 连续 2 年 < 0.5（业绩注水嫌疑）
    if len(fin.annual) >= 2:
        ratios = []
        for p in fin.annual[:2]:
            if p.operating_cashflow is not None and p.net_profit and p.net_profit > 0:
                ratios.append((p.period[:4], p.operating_cashflow / p.net_profit))
        if len(ratios) == 2 and all(r[1] < 0.5 for r in ratios):
            out.append(SellSignal(
                category="quality",
                severity="medium",
                label="CFO/净利持续低于 0.5",
                evidence=(f"经营现金流远低于净利润：{ratios[1][0]} {ratios[1][1]:.2f}，"
                          f"{ratios[0][0]} {ratios[0][1]:.2f}（应收账款激增 / 业绩质量存疑）"),
            ))

    return out


# ---------------------------------------------------------------------------
# ③ 估值离谱：当前价 vs 内在价值
# ---------------------------------------------------------------------------

def _valuation_extreme(snap: StockSnapshot) -> list[SellSignal]:
    """估值离谱信号。

    高 ROE 调整（方案 A）：
      ROE 5 年均 ≥ 25% 的"超优质企业"（茅台/伊利/泸州老窖级别）放宽阈值，
      因为 default profile 的 4 道关里 3 道偏 Graham deep value，对 ROE 30%+
      的伟大企业系统性偏严（DCF g=5% 太保守 + Graham PE×PB 用于优质企业过严）。
      参考巴菲特 / 芒格"以合理价格买伟大企业"范式。

      普通企业：discount < -50% high / < -30% medium
      高 ROE 企业（5y ≥ 25%）：discount < -80% high / < -50% medium
    """
    out: list[SellSignal] = []
    iv = snap.intrinsic
    if iv.discount is None:
        return out

    # 高 ROE 调整
    roe_5y_avg = _roe_5y_avg(snap)
    high_quality = roe_5y_avg is not None and roe_5y_avg >= 25
    if high_quality:
        threshold_high, threshold_medium = -80, -50
        roe_note = f"（高 ROE 企业 5y均 {roe_5y_avg:.1f}%，阈值已放宽）"
    else:
        threshold_high, threshold_medium = -50, -30
        roe_note = ""

    d = iv.discount

    if d <= threshold_high:
        out.append(SellSignal(
            category="valuation",
            severity="high",
            label="估值严重离谱",
            evidence=(f"当前市值高出内在价值 {-d:.0f}%（折价 {d:.1f}%），"
                      f"市值约为合理价值的 {snap.profile.total_market_cap / iv.fair_value:.1f} 倍"
                      f"（{iv.industry_view} 口径）{roe_note}"),
        ))
    elif d <= threshold_medium:
        out.append(SellSignal(
            category="valuation",
            severity="medium",
            label="估值显著偏贵",
            evidence=(f"当前市值高出内在价值 {-d:.0f}%（折价 {d:.1f}%），"
                      f"安全边际为负（{iv.industry_view} 口径）{roe_note}"),
        ))

    return out


def _roe_5y_avg(snap: StockSnapshot) -> Optional[float]:
    """近 5 年 ROE 均值，用于"伟大企业"识别。缺数据返回 None。"""
    roes = [p.roe for p in snap.financials.annual[:5] if p.roe is not None]
    if len(roes) < 3:
        return None
    return sum(roes) / len(roes)


# ---------------------------------------------------------------------------
# 行动建议降档（被 scorer._action 调用）
# ---------------------------------------------------------------------------

def degrade_action(base_action: str, signals: list[SellSignal],
                   total: int, discount: Optional[float]) -> str:
    """根据卖出信号给行动建议追加降档 / 警告。

    规则：
      - 有 high 严重度估值信号 → 「考虑减仓（估值严重离谱）」覆盖原档
      - 有 high 严重度生意/质量信号（多于 1 条）→ 在原档后追加 "⚠ 基本面/财务恶化"
      - medium 信号集成成 "⚠ N 项卖出信号（详见报告）"
    """
    if not signals:
        return base_action

    high_val = any(s.severity == "high" and s.category == "valuation" for s in signals)
    high_biz_or_qual = [s for s in signals if s.severity == "high"
                        and s.category in ("business", "quality")]
    medium_count = sum(1 for s in signals if s.severity == "medium")

    if high_val and total >= 70:
        # 估值严重离谱 + 还在 70+ 评级 → 强烈减仓信号
        out = "考虑减仓（估值严重离谱）"
    else:
        out = base_action

    warnings: list[str] = []
    if len(high_biz_or_qual) >= 1:
        cats = sorted({s.category for s in high_biz_or_qual})
        cat_label = "/".join({"business": "生意", "quality": "质量"}[c] for c in cats)
        warnings.append(f"⚠ {cat_label}恶化")
    if medium_count:
        warnings.append(f"⚠ {medium_count} 项卖出信号")

    if warnings:
        out = f"{out}  {' '.join(warnings)}"
    return out
