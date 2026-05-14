"""Quick screening：对一批股票仅用财务+估值数据做 0-30 分粗筛打分。

跟单股深度分析的区别：
  - 不调 LLM、不拉新闻、不拉治理事件、不拉股东
  - 只拉财报 + 估值（akshare 2 个调用 + baostock 副源）
  - 不做"安全边际折扣率"计算（成本高），只看通过几条粗筛规则
  - 每只 ~2-3s（缓存命中后 <0.5s）

quick_score 满 30 分：
  ROE 5 年均值  10 分（≥15%）
  PE TTM          5 分（≤25）
  PB              5 分（≤2.5；金融业 ≤1.5）
  负债率          5 分（<60%；金融业豁免）
  FCF/股 ≥ 0     5 分

硬否决（quick_flags 中标记，但不剔除 — 让用户自己看）：
  - 5 年内有亏损年
  - 负债率 > 80%（非金融）
  - PE×PB > 100（高估值预警）
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from stockwise.industry import IndustryLeader

_DB_PATH = Path.home() / ".stockwise" / "screen.db"


@dataclass
class QuickResult:
    code: str
    name: str
    industry: str
    industry_rank: int
    market_cap: Optional[float] = None
    profile_view: str = "default"
    pe: Optional[float] = None
    pb: Optional[float] = None
    roe_5y: Optional[float] = None
    debt_ratio: Optional[float] = None
    cfo_to_np: Optional[float] = None      # CFO / 净利润 比率（baostock CFOToNP）
    net_profit: Optional[float] = None
    quick_score: int = 0
    quick_flags: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# SQLite 索引
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS screen_index (
            code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            industry_rank INT,
            profile_view TEXT,
            market_cap REAL,
            pe REAL,
            pb REAL,
            roe_5y REAL,
            debt_ratio REAL,
            cfo_to_np REAL,
            net_profit REAL,
            quick_score INT,
            quick_flags TEXT,
            scanned_at REAL
        )
    """)
    # 兼容旧 schema（v0.5.0 有 fcf_per_share 列）
    try:
        conn.execute("ALTER TABLE screen_index ADD COLUMN cfo_to_np REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # already exists
    conn.commit()
    return conn


def save_quick_result(r: QuickResult) -> None:
    import json
    conn = _db()
    conn.execute("""
        REPLACE INTO screen_index
        (code, name, industry, industry_rank, profile_view, market_cap,
         pe, pb, roe_5y, debt_ratio, cfo_to_np, net_profit,
         quick_score, quick_flags, scanned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        r.code, r.name, r.industry, r.industry_rank, r.profile_view,
        r.market_cap, r.pe, r.pb, r.roe_5y, r.debt_ratio,
        r.cfo_to_np, r.net_profit,
        r.quick_score, json.dumps(r.quick_flags, ensure_ascii=False),
        time.time(),
    ))
    conn.commit()
    conn.close()


def load_quick_results(industry: Optional[str] = None,
                       min_score: Optional[int] = None,
                       limit: Optional[int] = None) -> list[dict]:
    import json
    conn = _db()
    sql = "SELECT * FROM screen_index WHERE 1=1"
    params: list = []
    if industry:
        sql += " AND industry LIKE ?"
        params.append(f"%{industry}%")
    if min_score is not None:
        sql += " AND quick_score >= ?"
        params.append(min_score)
    sql += " ORDER BY quick_score DESC, market_cap DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    cols = [d[0] for d in conn.execute(sql, params).description]
    conn.close()
    out = []
    for row in rows:
        d = dict(zip(cols, row))
        try:
            d["quick_flags"] = json.loads(d["quick_flags"] or "[]")
        except Exception:
            d["quick_flags"] = []
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Quick scoring
# ---------------------------------------------------------------------------

def quick_score(leader: IndustryLeader) -> QuickResult:
    """对单只股票做 quick scan：用 baostock 纯接口（不依赖东财）。

    数据来源：
      - baostock query_history_k_data_plus → close / peTTM / pbMRQ / psTTM
      - baostock query_profit_data → ROE (近 5 年)
      - baostock query_balance_data → 资产负债率
    """
    from stockwise.data.fetcher import classify_industry_view

    r = QuickResult(
        code=leader.code, name=leader.name,
        industry=leader.industry,
        industry_rank=leader.rank_in_industry,
        net_profit=leader.net_profit,
    )
    try:
        pe, pb, ps, close = _bs_latest_valuation(leader.bs_code)
        roes = _bs_roe_history(leader.bs_code, years=5)
        debt = _bs_latest_debt_ratio(leader.bs_code)
        cfo_np = _bs_cfo_to_np(leader.bs_code)
    except Exception as e:
        r.error = f"{type(e).__name__}: {e}"
        return r

    r.pe = pe
    r.pb = pb
    r.roe_5y = sum(roes) / len(roes) if roes else None
    r.debt_ratio = debt
    r.cfo_to_np = cfo_np

    # 用行业关键字做 profile classification（不需要 fin，只看 industry 字符串）
    r.profile_view = classify_industry_view(leader.industry, None)

    score, flags = _compute_quick_score(r)
    r.quick_score = score
    r.quick_flags = flags
    return r


def _bs_latest_valuation(bs_code: str) -> tuple:
    """从 baostock 拿最近 PE/PB/PS/收盘价。缓存 1 天。"""
    from stockwise.data.cache import cached_call
    from datetime import datetime, timedelta

    def _call():
        from stockwise.industry import _ensure_baostock_login
        import baostock as bs
        _ensure_baostock_login()
        end = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            bs_code, "date,close,peTTM,pbMRQ,psTTM",
            start_date=start, end_date=end, frequency="d",
        )
        df = rs.get_data()
        if df is None or df.empty:
            return None
        # 倒序找最近一行 PE 不为空的（当日盘前 PE 字段可能未填）
        for _, row in df.iloc[::-1].iterrows():
            pe = _to_float(row.get("peTTM"))
            if pe is not None:
                return (
                    pe,
                    _to_float(row.get("pbMRQ")),
                    _to_float(row.get("psTTM")),
                    _to_float(row.get("close")),
                )
        # 全空时只返回 close
        last = df.iloc[-1]
        return (None, None, None, _to_float(last.get("close")))
    result = cached_call("baostock:val_kline", bs_code, 24, _call, cache_none=True)
    return result if result else (None, None, None, None)


def _bs_roe_history(bs_code: str, years: int = 5) -> list[float]:
    """从 baostock 拿近 N 年 ROE。缓存 30 天。"""
    from stockwise.data.cache import cached_call
    from datetime import datetime

    def _call():
        from stockwise.industry import _ensure_baostock_login
        import baostock as bs
        _ensure_baostock_login()
        out = []
        current_year = datetime.today().year
        for year in range(current_year - years - 1, current_year):
            rs = bs.query_profit_data(code=bs_code, year=year, quarter=4)
            df = rs.get_data()
            if df is None or df.empty:
                continue
            roe_str = df.iloc[0].get("roeAvg")
            roe = _to_float(roe_str)
            if roe is not None:
                out.append(roe * 100)  # 0.30 → 30
        return out if out else None
    result = cached_call("baostock:roe_history", bs_code, 24 * 30, _call, cache_none=True)
    return result or []


def _bs_cfo_to_np(bs_code: str) -> Optional[float]:
    """baostock CFOToNP：经营现金流 / 净利润 比率。"""
    from stockwise.data.cache import cached_call
    from datetime import datetime

    def _call():
        from stockwise.industry import _ensure_baostock_login
        import baostock as bs
        _ensure_baostock_login()
        for year in range(datetime.today().year - 1, datetime.today().year - 4, -1):
            rs = bs.query_cash_flow_data(code=bs_code, year=year, quarter=4)
            df = rs.get_data()
            if df is not None and not df.empty:
                v = _to_float(df.iloc[0].get("CFOToNP"))
                if v is not None:
                    return v
        return None
    return cached_call("baostock:cfo_to_np", bs_code, 24 * 30, _call, cache_none=True)


def _bs_latest_debt_ratio(bs_code: str) -> Optional[float]:
    """从 baostock 拿最近资产负债率。缓存 30 天。"""
    from stockwise.data.cache import cached_call
    from datetime import datetime

    def _call():
        from stockwise.industry import _ensure_baostock_login
        import baostock as bs
        _ensure_baostock_login()
        for year in range(datetime.today().year - 1, datetime.today().year - 4, -1):
            rs = bs.query_balance_data(code=bs_code, year=year, quarter=4)
            df = rs.get_data()
            if df is not None and not df.empty:
                # baostock 的 liabilityToAsset 字段命名误导（实际是负债 YoY 增长率）
                # 用 assetToEquity 反推：负债率 = (AE - 1) / AE × 100
                ae = _to_float(df.iloc[0].get("assetToEquity"))
                if ae and ae > 1:
                    return (ae - 1) / ae * 100
        return None
    return cached_call("baostock:debt_ratio", bs_code, 24 * 30, _call, cache_none=True)


def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "nan":
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _compute_quick_score(r: QuickResult) -> tuple[int, list[str]]:
    """30 分制 + 硬否决标记。"""
    score = 0
    flags: list[str] = []
    is_fin = r.profile_view in ("bank", "insurance")

    # ROE 10 分
    if r.roe_5y is not None:
        if r.roe_5y >= 15:
            score += 10
        elif r.roe_5y >= 10:
            score += 6
        elif r.roe_5y >= 5:
            score += 3
    else:
        score += 3  # 数据缺失给中位

    # PE 5 分
    if r.pe is not None and r.pe > 0:
        if r.pe <= 15:
            score += 5
        elif r.pe <= 25:
            score += 4
        elif r.pe <= 50:
            score += 2

    # PB 5 分（金融业阈值更严）
    if r.pb is not None and r.pb > 0:
        threshold_ok = 1.5 if is_fin else 2.5
        threshold_mid = 1.0 if is_fin else 1.5
        if r.pb <= threshold_mid:
            score += 5
        elif r.pb <= threshold_ok:
            score += 3
        elif r.pb <= threshold_ok * 1.5:
            score += 1

    # 负债率 5 分（金融业豁免给中位）
    if is_fin:
        score += 3
    elif r.debt_ratio is not None:
        if r.debt_ratio <= 50:
            score += 5
        elif r.debt_ratio <= 60:
            score += 3
        elif r.debt_ratio <= 70:
            score += 1
        else:
            flags.append(f"⚠ 负债率 {r.debt_ratio:.0f}%")

    # CFO/净利润 ≥ 0.8 (5 分；金融业跳过给中位)
    if is_fin:
        score += 3
    elif r.cfo_to_np is not None:
        if r.cfo_to_np >= 0.8:
            score += 5
        elif r.cfo_to_np >= 0.5:
            score += 3
        elif r.cfo_to_np >= 0:
            score += 1
        else:
            flags.append(f"⚠ CFO/净利 {r.cfo_to_np:.2f}")
    else:
        score += 2  # 数据缺失给中位

    # 硬否决标记
    if r.pe and r.pb and r.pe * r.pb > 100:
        flags.append(f"⚠ PE×PB={r.pe*r.pb:.0f} 估值偏高")
    if r.net_profit is not None and r.net_profit <= 0:
        flags.append("⚠ 最近净利润 ≤ 0")

    return score, flags


def screen_hk(top_n: int = 3,
              industry_filter: Optional[list[str]] = None,
              exclude: Optional[list[str]] = None,
              progress_cb=None) -> list[QuickResult]:
    """港股 quick screen：从硬编码主流标的池拿 yfinance 数据，按 industry top N。"""
    from stockwise.hk_universe import all_hk_pool

    pool = all_hk_pool()
    results: list[QuickResult] = []
    for i, (code, name_hint) in enumerate(pool):
        r = _quick_score_hk(code, name_hint)
        if r is None or r.industry == "—":
            if progress_cb:
                progress_cb(i + 1, len(pool), phase="hk")
            continue
        # 行业过滤
        if industry_filter:
            from stockwise.industry import _expand_keywords
            keys = _expand_keywords(industry_filter)
            if not any(k.lower() in (r.industry or "").lower() for k in keys):
                if progress_cb:
                    progress_cb(i + 1, len(pool), phase="hk")
                continue
        if exclude:
            from stockwise.industry import _expand_keywords
            ex_keys = _expand_keywords(exclude)
            if any(k.lower() in (r.industry or "").lower() for k in ex_keys):
                if progress_cb:
                    progress_cb(i + 1, len(pool), phase="hk")
                continue
        results.append(r)
        if progress_cb:
            progress_cb(i + 1, len(pool), phase="hk")

    # 按 industry 分组取 top N（按 net_profit / market_cap 排序）
    by_industry: dict[str, list[QuickResult]] = {}
    for r in results:
        by_industry.setdefault(r.industry, []).append(r)
    out: list[QuickResult] = []
    for ind, items in by_industry.items():
        items.sort(key=lambda x: (x.market_cap or 0), reverse=True)
        for rank, r in enumerate(items[:top_n], 1):
            r.industry_rank = rank
            save_quick_result(r)
            out.append(r)
    return out


def _quick_score_hk(code: str, name_hint: str) -> Optional[QuickResult]:
    """对港股单只跑 quick scan：用 yfinance。"""
    from stockwise.data.cache import cached_call

    def _call():
        try:
            import yfinance as yf
        except ImportError:
            return None
        sym = f"{int(code):04d}.HK"
        try:
            info = yf.Ticker(sym).info
        except Exception:
            return None
        if not info or info.get("marketCap") is None:
            return None
        return info

    info = cached_call(f"yfinance:hk_info", code, 24, _call, cache_none=True)
    if not info:
        return None

    r = QuickResult(code=code, name=info.get("longName") or name_hint,
                     industry=info.get("industry") or "—",
                     industry_rank=0)
    r.market_cap = info.get("marketCap")
    r.pe = info.get("trailingPE")
    r.pb = info.get("priceToBook")
    roe_dec = info.get("returnOnEquity")
    r.roe_5y = roe_dec * 100 if roe_dec else None
    # debtToEquity 是百分数（如 50 表示 50%），转资产负债率
    dte = info.get("debtToEquity")
    if dte is not None:
        dte_decimal = dte / 100  # yfinance 给的是百分数 50 而非 0.5
        r.debt_ratio = dte_decimal / (1 + dte_decimal) * 100
    r.cfo_to_np = None
    r.profile_view = _hk_industry_to_view(r.industry)

    score, flags = _compute_quick_score(r)
    r.quick_score = score
    r.quick_flags = flags
    return r


def _hk_industry_to_view(industry: str) -> str:
    if not industry:
        return "default"
    s = industry.lower()
    if "bank" in s:
        return "bank"
    if "insurance" in s:
        return "insurance"
    if any(k in s for k in ("internet", "software", "semiconductors", "biotechnology")):
        return "growth"
    if any(k in s for k in ("oil", "gas", "mining", "steel", "shipping", "cement")):
        return "cyclical"
    return "default"


def screen_industry_leaders(top_n: int = 3,
                              industry_filter: Optional[list[str]] = None,
                              exclude: Optional[list[str]] = None,
                              workers: int = 4,
                              progress_cb=None,
                              cache_only: bool = False) -> list[QuickResult]:
    """端到端：发现行业头部 → 跑 quick scan → 写 SQLite → 返回结果。

    cache_only=True：跳过 baostock 拉取，只用已缓存数据。秒回。
    """
    from stockwise.industry import discover_leaders
    discovery = discover_leaders(top_n, industry_filter, exclude, workers, progress_cb,
                                  cache_only=cache_only)
    if discovery.error:
        return []

    results: list[QuickResult] = []
    total = len(discovery.leaders)
    for i, leader in enumerate(discovery.leaders):
        r = quick_score(leader)
        save_quick_result(r)
        results.append(r)
        if progress_cb:
            progress_cb(i + 1, total, phase="quick")
    return results
