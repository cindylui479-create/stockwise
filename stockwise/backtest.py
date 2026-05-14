"""screen 结果的简化回测：以指定日期为起点，看持有至今的收益。

不是严格意义的"历史回测"（需要在过去时点用当时的数据重新打分）；
而是更朴素的"业绩复盘"：如果当时按 screen 出的列表持有，至今表现如何。

回测对照：上证指数（sh.000001）/ 沪深 300（sh.000300）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from stockwise.data.cache import cached_call


@dataclass
class BacktestRow:
    code: str
    name: str
    price_start: Optional[float] = None
    price_end: Optional[float] = None
    return_pct: Optional[float] = None
    quick_score: Optional[int] = None


@dataclass
class BacktestResult:
    as_of: str
    horizon_end: str
    rows: list[BacktestRow] = field(default_factory=list)
    benchmark_return: Optional[float] = None
    portfolio_return: Optional[float] = None    # 等权平均收益
    benchmark_label: str = "沪深 300"
    error: Optional[str] = None

    @property
    def alpha(self) -> Optional[float]:
        if self.portfolio_return is None or self.benchmark_return is None:
            return None
        return self.portfolio_return - self.benchmark_return


def run_backtest(as_of: str, codes_with_names: list[tuple],
                 horizon_end: Optional[str] = None,
                 quick_scores: Optional[dict] = None) -> BacktestResult:
    """对一组股票计算 as_of → horizon_end 的收益率，并与沪深 300 对比。"""
    from datetime import date, timedelta

    if horizon_end is None:
        horizon_end = date.today().isoformat()

    result = BacktestResult(as_of=as_of, horizon_end=horizon_end)

    for code, name in codes_with_names:
        bs_code = ("sh." if code.startswith("6") else "sz.") + code
        price_start = _bs_price_at(bs_code, as_of)
        price_end = _bs_price_at(bs_code, horizon_end)
        row = BacktestRow(
            code=code, name=name,
            price_start=price_start, price_end=price_end,
            quick_score=quick_scores.get(code) if quick_scores else None,
        )
        if price_start and price_end and price_start > 0:
            row.return_pct = (price_end - price_start) / price_start * 100
        result.rows.append(row)

    # 等权平均收益
    valid_returns = [r.return_pct for r in result.rows if r.return_pct is not None]
    if valid_returns:
        result.portfolio_return = sum(valid_returns) / len(valid_returns)

    # 沪深 300 收益（基准）
    bm_start = _bs_price_at("sh.000300", as_of)
    bm_end = _bs_price_at("sh.000300", horizon_end)
    if bm_start and bm_end:
        result.benchmark_return = (bm_end - bm_start) / bm_start * 100

    return result


def _bs_price_at(bs_code: str, target_date: str) -> Optional[float]:
    """在 target_date 前后 14 天内取最近的收盘价。缓存 30 天。"""
    from datetime import datetime, timedelta

    def _call():
        from stockwise.industry import _ensure_baostock_login
        import baostock as bs
        _ensure_baostock_login()
        t = datetime.strptime(target_date, "%Y-%m-%d")
        start = (t - timedelta(days=14)).strftime("%Y-%m-%d")
        end = (t + timedelta(days=14)).strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            bs_code, "date,close",
            start_date=start, end_date=end, frequency="d",
        )
        df = rs.get_data()
        if df is None or df.empty:
            return None
        # 找最接近 target_date 的那条
        df = df.sort_values("date")
        target = datetime.strptime(target_date, "%Y-%m-%d")
        best_diff = None
        best_close = None
        for _, row in df.iterrows():
            d = datetime.strptime(row["date"], "%Y-%m-%d")
            diff = abs((d - target).days)
            close = row.get("close")
            if close and close != "":
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    try:
                        best_close = float(close)
                    except (TypeError, ValueError):
                        continue
        return best_close
    return cached_call(f"baostock:price_at:{target_date}", bs_code, 24 * 30, _call,
                       cache_none=True)
