from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from stockwise.analyzer.llm import LLMAnalysis
from stockwise.analyzer.scorer import ScoreResult, score as score_fn
from stockwise.data.models import StockSnapshot

_TEMPLATE_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(disabled_extensions=("md", "j2")),
    trim_blocks=False,
    lstrip_blocks=False,
)


def _format_money(value: Optional[float], currency: str = "CNY") -> str:
    if value is None:
        return "—"
    abs_v = abs(value)
    unit, divisor = ("亿", 1e8) if abs_v >= 1e8 else ("万", 1e4) if abs_v >= 1e4 else ("", 1)
    sym = {"CNY": "¥", "HKD": "HK$"}.get(currency, "")
    return f"{sym}{value/divisor:,.2f}{unit}"


def _format_price(value: Optional[float], currency: str = "CNY") -> str:
    if value is None:
        return "—"
    sym = {"CNY": "¥", "HKD": "HK$"}.get(currency, "")
    return f"{sym}{value:,.2f}"


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def _format_num(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def render(snapshot: StockSnapshot, base_score: ScoreResult,
           llm: Optional[LLMAnalysis], llm_error: Optional[str] = None) -> str:
    """生成 Markdown 报告。

    若 LLM 给出 business_understandability / management_quality，
    把它们替代默认中位分，重算总分与评级。
    llm_error：LLM 调用失败时的错误描述。模板用它区分「未启用」vs「调用失败」。
    """
    final_score = _merge_score_with_llm(snapshot, base_score, llm)
    template = _env.get_template("template.md.j2")
    market_label = "A 股" if snapshot.profile.market == "A" else "港股"

    # 计算「买下整家公司」相关数字
    buyout = _compute_buyout_metrics(snapshot)

    return template.render(
        today=date.today().isoformat(),
        market_label=market_label,
        profile=snapshot.profile,
        financials=snapshot.financials,
        valuation=snapshot.valuation,
        intrinsic=snapshot.intrinsic,
        dividends=snapshot.dividends,
        validation=snapshot.validation,
        governance=snapshot.governance,
        holders=snapshot.holders,
        news=snapshot.news,
        score=final_score,
        llm=llm,
        llm_error=llm_error,
        buyout=buyout,
        snap_industry_cycle=getattr(snapshot, "industry_cycle", None),
        snap_industry_roe=getattr(snapshot, "industry_roe_rank", None),
        format_money=_format_money,
        format_price=_format_price,
        format_pct=_format_pct,
        format_num=_format_num,
    )


def _merge_score_with_llm(snapshot: StockSnapshot, base: ScoreResult,
                          llm: Optional[LLMAnalysis]) -> ScoreResult:
    if llm is None:
        return base
    return score_fn(
        snapshot,
        llm_business_score=llm.business_understandability,
        llm_management_score=llm.management_quality,
    )


def _compute_buyout_metrics(snapshot: StockSnapshot):
    """如果你买下整家公司，几年回本？"""
    p = snapshot.profile
    fin = snapshot.financials
    iv = snapshot.intrinsic
    out = {
        "market_cap": p.total_market_cap,
        "annual_profit": None,
        "payback_by_profit": None,
        "annual_fcf": None,
        "payback_by_fcf": None,
    }
    if not fin.annual:
        return out
    last3_profits = [pp.net_profit for pp in fin.annual[:3] if pp.net_profit]
    if last3_profits and p.total_market_cap:
        avg_profit = sum(last3_profits) / len(last3_profits)
        out["annual_profit"] = avg_profit
        out["payback_by_profit"] = p.total_market_cap / avg_profit if avg_profit else None

    last3_fcfs = [pp.fcf_per_share for pp in fin.annual[:3] if pp.fcf_per_share is not None]
    shares = p.shares
    if last3_fcfs and shares and p.total_market_cap:
        avg_fcf_total = sum(last3_fcfs) / len(last3_fcfs) * shares
        out["annual_fcf"] = avg_fcf_total
        out["payback_by_fcf"] = p.total_market_cap / avg_fcf_total if avg_fcf_total else None
    return out


def write(report_md: str, code: str, out_dir: Path, name: Optional[str] = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name_part = _sanitize_filename(name) if name and name != code else ""
    suffix = f"_{name_part}" if name_part else ""
    path = out_dir / f"{code}{suffix}_{date.today().isoformat()}.md"
    path.write_text(report_md, encoding="utf-8")
    return path


def _sanitize_filename(s: str) -> str:
    """文件名安全清理：替换非法字符；保留中文。"""
    import re
    # Windows / Linux 文件系统通用非法字符：/ \ : * ? " < > |
    s = re.sub(r'[\\/:\*\?"<>\|]', '_', s)
    s = s.strip().replace(" ", "_")
    return s[:30]  # 限制长度
