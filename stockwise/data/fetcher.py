from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Callable, Optional, TypeVar

import akshare as ak
import pandas as pd

_T = TypeVar("_T")


def _retry(fn: Callable[[], _T], attempts: int = 3, delay: float = 1.5,
           swallow: bool = False) -> Optional[_T]:
    """对 akshare 偶发的空响应/JSON 错误做简单重试。"""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    if swallow:
        return None
    raise last  # type: ignore[misc]

from stockwise.data.market import StockId
from stockwise.data.models import (
    CompanyProfile,
    DividendInfo,
    DividendRecord,
    Financials,
    FinancialPeriod,
    IntrinsicValue,
    NewsItem,
    StockSnapshot,
    ValidationReport,
    ValueGate,
    Valuation,
)


class FetchError(RuntimeError):
    pass


# 新体系拉 10 年数据（不到 10 年也兼容）
DEFAULT_YEARS = 10


def fetch(stock_id: StockId, validate: bool = True, governance: bool = True,
          holders: bool = True) -> StockSnapshot:
    if stock_id.market == "A":
        snap = _fetch_a(stock_id.code)
        yf_info = None
    else:
        snap = _fetch_hk(stock_id.code)
        yf_info = _hk_yf_info(stock_id.code)  # 复用，给 holders 用
    if validate:
        from stockwise.data.validator import validate as run_validate
        snap.validation = run_validate(stock_id.code, stock_id.market, snap.financials)
    else:
        snap.validation = ValidationReport(skipped=True, error="用户禁用 (--no-validate)")
    if governance:
        from stockwise.data.governance import fetch_events
        snap.governance = fetch_events(stock_id.code, stock_id.market)
    if holders:
        from stockwise.data.holders import fetch_holders
        snap.holders = fetch_holders(stock_id.code, stock_id.market, yf_info)
    # v0.9：行业周期位置（仅周期/消费/医药/金融启用映射，其他静默跳过）
    try:
        from stockwise.data.industry_cycle import fetch_industry_cycle
        snap.industry_cycle = fetch_industry_cycle(snap.profile.industry)
    except Exception:
        pass
    # v0.11 #51：行业 ROE 横截面分位（首次行业拉取慢，缓存 72h 后秒回）
    try:
        if stock_id.market == "A":
            from stockwise.data.industry_roe import fetch_industry_roe_rank
            roes = [p.roe for p in snap.financials.annual[:5] if p.roe is not None]
            company_roe_5y = sum(roes) / len(roes) if roes else None
            snap.industry_roe_rank = fetch_industry_roe_rank(
                stock_id.code, snap.profile.industry, company_roe_5y)
    except Exception:
        pass
    # v0.13 #59：主营构成 / 关联企业
    try:
        from stockwise.data.business_segments import fetch_segments
        snap.business_segments = fetch_segments(stock_id.code, stock_id.market)
    except Exception:
        pass
    return snap


# ---------------------------------------------------------------------------
# A 股
# ---------------------------------------------------------------------------

def _fetch_a(code: str) -> StockSnapshot:
    profile = _a_profile(code)
    financials = _a_financials(code, years=DEFAULT_YEARS)
    # Tushare Pro 增强（如设置了 TUSHARE_TOKEN 则填充研发/capex 字段）
    from stockwise.data.tushare_extra import enrich as _ts_enrich
    _ts_enrich(code, "A", financials)
    valuation = _a_valuation(code)
    dividends = _a_dividends(code, financials)
    intrinsic = compute_intrinsic_value(profile, financials, valuation, dividends)
    news = _news(code)
    return StockSnapshot(
        profile=profile,
        financials=financials,
        valuation=valuation,
        dividends=dividends,
        intrinsic=intrinsic,
        news=news,
    )


def _a_profile(code: str) -> CompanyProfile:
    """三层后备：
      1. 东财 push2 (stock_individual_info_em) —— 名称/行业/市值/上市日期
      2. 东财 emweb f10 接口 —— 名称/上市日期（push2 502 时可用）
      3. sina daily —— 价格/股本（最稳定）
    """
    info: dict = {}
    em_df = _retry(lambda: ak.stock_individual_info_em(symbol=code), swallow=True)
    if em_df is not None and not em_df.empty:
        info = dict(zip(em_df["item"], em_df["value"]))

    # 价格：先东财，再 sina
    price = _a_latest_price(code)
    sina_shares: Optional[float] = None
    if price is None or not info:
        sina = _a_sina_daily(code)
        if sina:
            sina_shares = sina.get("outstanding_share")
            if price is None:
                price = sina.get("close")

    # 名称/上市日期/行业：优先 push2 信息；缺失时走 f10 后备
    name = str(info.get("股票简称")) if info.get("股票简称") else None
    listing_date = str(info.get("上市时间")) if info.get("上市时间") else None
    industry = str(info.get("行业")) if info.get("行业") else None
    if name is None or listing_date is None or industry is None:
        f10 = _a_f10_basics(code)
        if f10:
            name = name or f10.get("name")
            listing_date = listing_date or f10.get("listing_date")
            industry = industry or f10.get("industry")
    name = name or code

    market_cap = _to_float(info.get("总市值"))
    shares = (market_cap / price) if (market_cap and price) else sina_shares
    if market_cap is None and shares and price:
        market_cap = shares * price

    return CompanyProfile(
        code=code,
        market="A",
        name=name,
        industry=industry,
        total_market_cap=market_cap,
        float_market_cap=_to_float(info.get("流通市值")),
        listing_date=listing_date,
        current_price=price,
        currency="CNY",
        shares_outstanding=shares,
    )


def _a_f10_basics(code: str) -> Optional[dict]:
    """东财 emweb f10 后备：拿名称 + 上市日期 + 行业。push2 502 时仍可用。"""
    import requests
    prefix = "SH" if code[0] == "6" else "SZ"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={prefix}{code}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    out: dict = {}
    if data.get("jbzl"):
        jb = data["jbzl"][0]
        out["name"] = jb.get("SECURITY_NAME_ABBR")
        # INDUSTRYCSRC1 形如 "金融业-保险业"；EM2016 形如 "金融-非银行金融-保险"
        out["industry"] = jb.get("INDUSTRYCSRC1") or jb.get("EM2016")
    if data.get("fxxg"):
        listing = data["fxxg"][0].get("LISTING_DATE")
        if listing:
            out["listing_date"] = str(listing).split(" ")[0]
    return out or None


def _a_latest_price(code: str) -> Optional[float]:
    end = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=15)).strftime("%Y%m%d")
    df = _retry(
        lambda: ak.stock_zh_a_hist(symbol=code, period="daily",
                                   start_date=start, end_date=end, adjust=""),
        swallow=True,
    )
    if df is None or df.empty:
        return None
    return float(df["收盘"].iloc[-1])


def _a_sina_daily(code: str) -> Optional[dict]:
    """sina 后备：返回 close / outstanding_share / name（拼不到 name 时为 None）。"""
    sym = ("sh" if code[0] in "6" else "sz") + code
    end = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=15)).strftime("%Y%m%d")
    df = _retry(
        lambda: ak.stock_zh_a_daily(symbol=sym, start_date=start, end_date=end, adjust=""),
        swallow=True,
    )
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    return {
        "close": _to_float(last.get("close")),
        "outstanding_share": _to_float(last.get("outstanding_share")),
        "name": None,  # sina daily 接口不带名称
    }


# A 股财务摘要中的指标名 → 我们字段名
_A_INDICATOR_MAP = {
    "营业总收入": "revenue",
    "归母净利润": "net_profit",
    "净资产收益率(ROE)": "roe",
    "毛利率": "gross_margin",
    "销售净利率": "net_margin",
    "资产负债率": "debt_ratio",
    "经营现金流量净额": "operating_cashflow",
    "商誉": "goodwill",
    "每股企业自由现金流量": "fcf_per_share",
    # v0.11 #52：利润质量深度分解
    "应收账款": "accounts_receivable",
    "合同负债": "contract_liabilities",
    "预收款项": "prepayments",
}


def _a_balance_sheet_enrich(code: str, fin: Financials) -> None:
    """v0.11 #52 fix：从东财资产负债表接口拉应收账款 / 合同负债填充到 fin。

    stock_financial_abstract 返回的"常用指标"不含资产负债表科目，需要
    stock_balance_sheet_by_report_em（英文列名：ACCOUNTS_RECE / CONTRACT_LIAB）。
    """
    from stockwise.data.cache import cached_call, TTL_FINANCIALS
    if not fin.annual:
        return
    try:
        prefix = "SH" if code.startswith("6") else "SZ"
        symbol = f"{prefix}{code}"
        df = cached_call(
            "em:balance_sheet", symbol, TTL_FINANCIALS,
            lambda: ak.stock_balance_sheet_by_report_em(symbol=symbol),
        )
    except Exception:
        return
    if df is None or df.empty:
        return

    # 建立 REPORT_DATE (YYYY-MM-DD) → row 索引（只取 12-31 年报）
    import pandas as pd
    df = df.copy()
    df["REPORT_DATE_STR"] = df["REPORT_DATE"].astype(str).str[:10]
    annual_rows = df[df["REPORT_DATE_STR"].str.endswith("-12-31")]
    by_date = {row["REPORT_DATE_STR"]: row for _, row in annual_rows.iterrows()}

    for p in fin.annual:
        # period 是 "20251231"，转换为 "2025-12-31"
        if len(p.period) >= 8:
            date_str = f"{p.period[:4]}-{p.period[4:6]}-{p.period[6:8]}"
            row = by_date.get(date_str)
            if row is None:
                continue
            p.accounts_receivable = _to_float(row.get("ACCOUNTS_RECE"))
            p.contract_liabilities = _to_float(row.get("CONTRACT_LIAB"))
            p.prepayments = _to_float(row.get("ADVANCE_RECEIVABLES"))


def _a_financials(code: str, years: int = 10) -> Financials:
    from stockwise.data.cache import cached_call, TTL_FINANCIALS
    df = cached_call(
        "akshare:stock_financial_abstract", code, TTL_FINANCIALS,
        lambda: _retry(lambda: ak.stock_financial_abstract(symbol=code), swallow=True),
    )
    if df is None or df.empty:
        return Financials()

    period_cols = [c for c in df.columns if isinstance(c, str) and re.fullmatch(r"\d{4}1231", c)]
    period_cols.sort(reverse=True)
    period_cols = period_cols[:years]

    common = df[df["选项"] == "常用指标"] if "选项" in df.columns else df
    per_share = df[df["选项"] == "每股指标"] if "选项" in df.columns else df

    by_indicator: dict[str, pd.Series] = {}
    for indicator, fld in _A_INDICATOR_MAP.items():
        # fcf_per_share 在「每股指标」里，其他在「常用指标」里
        primary = per_share if fld == "fcf_per_share" else common
        match = primary[primary["指标"] == indicator]
        if match.empty:
            match = df[df["指标"] == indicator]
        if not match.empty:
            by_indicator[fld] = match.iloc[0]

    annual: list[FinancialPeriod] = []
    for i, col in enumerate(period_cols):
        period = FinancialPeriod(period=col)
        for fld, row in by_indicator.items():
            v = row.get(col)
            parsed = _to_float(v)
            # 商誉行存在但值为 NaN/空 → 该期账上无商誉（区别于"该字段未抓到"）。
            # 茅台等公司从不靠并购扩张，goodwill 行整列 NaN，应视为 0 而非缺失。
            if fld == "goodwill" and parsed is None:
                parsed = 0.0
            setattr(period, fld, parsed)
        if i + 1 < len(period_cols):
            prev_col = period_cols[i + 1]
            period.revenue_yoy = _yoy(by_indicator.get("revenue"), col, prev_col)
            period.profit_yoy = _yoy(by_indicator.get("net_profit"), col, prev_col)
        annual.append(period)
    fin = Financials(annual=annual)
    # v0.11 #52：补充资产负债表科目（应收账款 / 合同负债）
    _a_balance_sheet_enrich(code, fin)
    return fin


def _a_valuation(code: str) -> Valuation:
    try:
        df = ak.stock_value_em(symbol=code)
    except Exception:
        return Valuation()
    if df is None or df.empty:
        return Valuation()
    df = df.copy()
    df["数据日期"] = pd.to_datetime(df["数据日期"], errors="coerce")
    df = df.dropna(subset=["数据日期"]).sort_values("数据日期")

    cutoff = df["数据日期"].max() - pd.Timedelta(days=5 * 365)
    recent = df[df["数据日期"] >= cutoff]

    current_pe = _to_float(df["PE(TTM)"].iloc[-1])
    current_pb = _to_float(df["市净率"].iloc[-1])
    current_ps = _to_float(df["市销率"].iloc[-1])

    return Valuation(
        pe_ttm=current_pe,
        pb=current_pb,
        ps=current_ps,
        pe_percentile_5y=_percentile(recent["PE(TTM)"], current_pe),
        pb_percentile_5y=_percentile(recent["市净率"], current_pb),
        ps_percentile_5y=_percentile(recent["市销率"], current_ps),
        has_history=True,
    )


def _a_dividends(code: str, fin: Financials) -> DividendInfo:
    """拉历史分红，结合财报算连续派息年数 + TTM 派息合计。"""
    from stockwise.data.cache import cached_call, TTL_DIVIDENDS
    try:
        df = cached_call(
            "akshare:stock_history_dividend_detail", code, TTL_DIVIDENDS,
            lambda: ak.stock_history_dividend_detail(symbol=code, indicator="分红"),
        )
    except Exception:
        return DividendInfo()
    if df is None or df.empty:
        return DividendInfo()

    df = df.copy()
    df = df[df["进度"] == "实施"]
    df = df[pd.notna(df.get("除权除息日"))]
    df = df.dropna(subset=["派息"])
    if df.empty:
        return DividendInfo()
    df["除权日"] = pd.to_datetime(df["除权除息日"], errors="coerce")
    df = df.dropna(subset=["除权日"])
    df["年份"] = df["除权日"].dt.year

    yearly = df.groupby("年份")["派息"].sum().reset_index()
    yearly = yearly.sort_values("年份", ascending=False)
    history = [
        DividendRecord(year=int(r["年份"]), cash_per_10_shares=float(r["派息"]))
        for _, r in yearly.iterrows()
    ]

    # 连续派息年数：从最近一年回推，年份要连续
    consecutive = 0
    expected = None
    for rec in history:
        if expected is None:
            expected = rec.year
        if rec.year == expected:
            consecutive += 1
            expected -= 1
        else:
            break

    # 派息额（每 10 股）：取"近 1 个完整财务年度"的总额（v0.8 改正）
    # 旧实现按"除权日近 365 天"求和，在年中跨财年会把当年中期派息 + 上一年年报派息混在一起，
    # 导致泸州老窖等"2026 派 13.58 vs 2025 派 59.5" 4 倍跳变。
    # 现在：找 history 中 year < 当前年 的最近一项；当前年≥10 月时用当年（年内派息基本完成）。
    now = pd.Timestamp.now()
    ttm_per_10 = None
    for rec in history:
        if rec.year < now.year:
            ttm_per_10 = rec.cash_per_10_shares
            break
        if rec.year == now.year and now.month >= 10:
            ttm_per_10 = rec.cash_per_10_shares
            break

    return DividendInfo(
        history=history,
        consecutive_years=consecutive,
        avg_payout_5y=None,
        ttm_per_10_shares=ttm_per_10,
    )


# ---------------------------------------------------------------------------
# 港股
# ---------------------------------------------------------------------------

def _fetch_hk(code: str) -> StockSnapshot:
    yf_info = _hk_yf_info(code)
    profile = _hk_profile(code, yf_info)
    financials, currency = _hk_financials(code, years=DEFAULT_YEARS)
    if currency:
        profile = CompanyProfile(**{**profile.__dict__, "currency": currency})
    valuation = _hk_valuation(code, profile, financials, yf_info)
    dividends = _hk_dividends(yf_info, profile)
    intrinsic = compute_intrinsic_value(profile, financials, valuation, dividends)
    news = _news(f"{code}.HK")
    return StockSnapshot(
        profile=profile,
        financials=financials,
        valuation=valuation,
        dividends=dividends,
        intrinsic=intrinsic,
        news=news,
    )


def _hk_dividends(yf_info: Optional[dict], profile: CompanyProfile) -> DividendInfo:
    """港股分红：从 yfinance 的 dividendYield + 当前价 反推每 10 股 TTM 派息。"""
    if not yf_info or not profile.current_price:
        return DividendInfo()
    yld = _to_float(yf_info.get("dividendYield"))
    if yld is None or yld <= 0:
        return DividendInfo()
    # yfinance 0.2+ 的 dividendYield 是百分数（如 4.92 表示 4.92%）
    per_10 = yld * profile.current_price * 10 / 100
    return DividendInfo(ttm_per_10_shares=per_10)


def _hk_yf_info(code: str) -> Optional[dict]:
    """yfinance 港股一站式数据。yfinance 港股 ticker 必须是精确 4 位 + .HK
    （腾讯 0700.HK 而非 00700.HK），需要把 5 位 akshare 码 lstrip 后再 pad。
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        sym = f"{int(code):04d}.HK"
    except ValueError:
        return None
    try:
        info = yf.Ticker(sym).info
    except Exception:
        return None
    # yfinance 对不存在的码返回 dict 但 marketCap 缺失，用此做识别
    if not info or info.get("marketCap") is None:
        return None
    return info


def _hk_profile(code: str, yf_info: Optional[dict] = None) -> CompanyProfile:
    try:
        df = ak.stock_hk_security_profile_em(symbol=code)
    except Exception:
        df = None
    name = code
    listing = None
    if df is not None and not df.empty:
        row = df.iloc[0]
        name = str(row.get("证券简称", code))
        listing = str(row.get("上市日期", "")) or None

    # yfinance 覆盖 name/industry/market_cap/shares（若可用）
    industry: Optional[str] = None
    market_cap: Optional[float] = None
    shares: Optional[float] = None
    currency = "HKD"
    if yf_info:
        # 优先用 yfinance 的 longName/shortName（如果 name 还是 code）
        if name == code:
            name = str(yf_info.get("longName") or yf_info.get("shortName") or code)
        industry = yf_info.get("industry") or yf_info.get("sector")
        market_cap = _to_float(yf_info.get("marketCap"))
        shares = _to_float(yf_info.get("sharesOutstanding"))
        currency = str(yf_info.get("currency") or "HKD")

    price = _hk_latest_price(code, yf_info)
    if market_cap is None and shares and price:
        market_cap = shares * price
    return CompanyProfile(
        code=code,
        market="HK",
        name=name,
        industry=industry,
        total_market_cap=market_cap,
        float_market_cap=None,
        listing_date=listing,
        current_price=price,
        currency=currency,
        shares_outstanding=shares,
    )


def _hk_latest_price(code: str, yf_info: Optional[dict] = None) -> Optional[float]:
    # yfinance 已有 info 时直接用，避免再调东财
    if yf_info:
        for key in ("currentPrice", "regularMarketPrice", "previousClose"):
            v = _to_float(yf_info.get(key))
            if v:
                return v
    end = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=15)).strftime("%Y%m%d")
    try:
        df = ak.stock_hk_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return float(df["收盘"].iloc[-1])


def _hk_financials(code: str, years: int = 10) -> tuple[Financials, Optional[str]]:
    try:
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=code, indicator="年度")
    except Exception:
        return Financials(), None
    if df is None or df.empty:
        return Financials(), None
    df = df.sort_values("REPORT_DATE", ascending=False).head(years).reset_index(drop=True)
    annual: list[FinancialPeriod] = []
    for _, r in df.iterrows():
        period_str = str(r.get("REPORT_DATE", "")).replace("-", "").split(" ")[0][:8]
        annual.append(FinancialPeriod(
            period=period_str,
            revenue=_to_float(r.get("OPERATE_INCOME")),
            net_profit=_to_float(r.get("HOLDER_PROFIT")),
            roe=_to_float(r.get("ROE_AVG")),
            gross_margin=_to_float(r.get("GROSS_PROFIT_RATIO")),
            net_margin=_to_float(r.get("NET_PROFIT_RATIO")),
            debt_ratio=_to_float(r.get("DEBT_ASSET_RATIO")),
            operating_cashflow=None,
            free_cashflow=None,
            fcf_per_share=None,
            goodwill=None,
            revenue_yoy=_to_float(r.get("OPERATE_INCOME_YOY")),
            profit_yoy=_to_float(r.get("HOLDER_PROFIT_YOY")),
        ))
    currency = str(df.iloc[0].get("CURRENCY", "HKD")) if "CURRENCY" in df.columns else "HKD"
    fin = Financials(annual=annual)
    fin._latest_eps_ttm = _to_float(df.iloc[0].get("EPS_TTM"))  # type: ignore[attr-defined]
    fin._latest_bps = _to_float(df.iloc[0].get("BPS"))  # type: ignore[attr-defined]
    fin._is_cny = bool(df.iloc[0].get("IS_CNY_CODE"))  # type: ignore[attr-defined]
    return fin, currency


def _hk_valuation(code: str, profile: CompanyProfile, fin: Financials,
                  yf_info: Optional[dict] = None) -> Valuation:
    """港股估值三级后备：
      1. yfinance（首选）—— trailingPE / priceToBook / priceToSalesTrailing12Months
      2. 百度股市通 —— stock_hk_valuation_baidu（含 5 年分位）
      3. 当前价 / EPS_TTM 兜底（仅在财报币种与股价币种一致时）
    """
    pe = pb = ps = None
    if yf_info:
        pe = _to_float(yf_info.get("trailingPE"))
        pb = _to_float(yf_info.get("priceToBook"))
        ps = _to_float(yf_info.get("priceToSalesTrailing12Months"))

    # 走百度拿历史分位（仅 PE/PB）
    pe_pct = pb_pct = None
    try:
        pe_df = ak.stock_hk_valuation_baidu(symbol=code, indicator="市盈率(TTM)", period="近五年")
        if pe_df is not None and not pe_df.empty:
            if pe is None:
                pe = _to_float(pe_df["value"].iloc[-1])
            pe_pct = _percentile(pe_df["value"], pe)
        pb_df = ak.stock_hk_valuation_baidu(symbol=code, indicator="市净率", period="近五年")
        if pb_df is not None and not pb_df.empty:
            if pb is None:
                pb = _to_float(pb_df["value"].iloc[-1])
            pb_pct = _percentile(pb_df["value"], pb)
    except Exception:
        pass

    if pe is not None or pb is not None:
        return Valuation(
            pe_ttm=pe, pb=pb, ps=ps,
            pe_percentile_5y=pe_pct, pb_percentile_5y=pb_pct,
            has_history=pe_pct is not None or pb_pct is not None,
        )

    # 最后一道：用每股财务指标兜底（仅当 yfinance + 百度都失败）
    if profile.current_price is None or not fin.annual:
        return Valuation()
    is_cny = getattr(fin, "_is_cny", False)
    if is_cny:
        return Valuation(has_history=False)
    eps_ttm = getattr(fin, "_latest_eps_ttm", None)
    bps = getattr(fin, "_latest_bps", None)
    pe = profile.current_price / eps_ttm if eps_ttm and eps_ttm > 0 else None
    pb = profile.current_price / bps if bps and bps > 0 else None
    return Valuation(pe_ttm=pe, pb=pb, has_history=False)


# ---------------------------------------------------------------------------
# 内在价值估算
# ---------------------------------------------------------------------------

# ---- 默认体系阈值 ----
FCF_YIELD_THRESHOLD = 6.0
GRAHAM_BASE_THRESHOLD = 22.5
OWNER_EARNINGS_MULT = 12
DCF_DISCOUNT_RATE = 0.08
DCF_MAX_GROWTH = 0.05
DCF_MIN_GROWTH = 0.0

# ---- 银行/保险阈值（巴菲特实战标准）----
BANK_PB_THRESHOLD = 1.5             # 巴菲特买 BAC 时 P/B 0.6；1.5 是底线
BANK_DIV_YIELD_THRESHOLD = 4.0      # 银行成熟期股息率底线
INS_PB_THRESHOLD = 1.5
INS_DIV_YIELD_THRESHOLD = 3.0
IMPLIED_RETURN_THRESHOLD = 10.0     # ROE/PB 隐含回报率 ≥ 10%


_GROWTH_INDUSTRY_KEYS = (
    "软件", "计算机", "互联网", "信息技术",
    "通信设备", "电子设备", "半导体",
    "医药制造", "生物制品", "医疗器械",
    "研究和试验发展",
    "电气机械",  # 锂电、新能源设备
    "汽车制造",  # 新能源车
    # 英文（港股/美股）
    "software", "internet", "semiconductors", "biotechnology", "pharmaceutical",
)

# 周期股关键字（用 Shiller PE 替代静态 PE，避免周期顶低 PE 陷阱）
_CYCLICAL_INDUSTRY_KEYS = (
    "煤炭", "钢铁", "黑色金属", "石油", "天然气", "采矿", "采选",
    "有色金属", "稀有金属", "稀土",
    "化学原料", "化学纤维", "基础化工",
    "造纸", "水泥", "玻璃", "建材",
    "航运", "港口", "运输",  # 航运周期；港口 / 公路较稳但姑且归类
    # 英文
    "steel", "mining", "oil", "gas", "metals", "shipping", "cement",
)

# 排除：强周期/资源/传统行业不归 growth，即使 CAGR 暂时高也只是周期上行
_NON_GROWTH_KEYS = _CYCLICAL_INDUSTRY_KEYS + (
    "房地产", "建筑", "农林牧渔", "渔业", "畜牧",
    "银行", "保险", "证券", "金融",
)


def classify_industry_view(industry: Optional[str], fin: Optional["Financials"] = None) -> str:
    """映射到 default / bank / insurance / growth / cyclical。

    优先级：金融业 > 周期股 > 成长股 > 默认。
    """
    if not industry:
        return "default"
    s = industry.lower()
    if "银行" in industry or "货币金融服务" in industry or "bank" in s:
        return "bank"
    if "保险" in industry or "insurance" in s:
        return "insurance"

    # 周期股：资源/原材料/运输等
    if any(k.lower() in s for k in _CYCLICAL_INDUSTRY_KEYS):
        return "cyclical"

    cagr = _industry_rev_cagr(fin)

    # growth A：行业 + CAGR ≥ 15%
    is_growth_industry = any(k.lower() in s for k in _GROWTH_INDUSTRY_KEYS)
    if is_growth_industry and cagr is not None and cagr >= 0.15:
        return "growth"

    # growth B：纯财务特征 — CAGR ≥ 25% 且不在排除类
    if cagr is not None and cagr >= 0.25:
        if not any(k in industry for k in _NON_GROWTH_KEYS):
            return "growth"

    # semi_growth (v0.8)：CAGR 12-15% + 毛利 5y均 ≥ 30% + 非排除类
    #   覆盖再投资期/中等成长的优质消费医药等（ROE 12-18%、稳定毛利）。
    #   避免 default profile 的 ROE ≥ 15% 严格门槛误杀。
    #   估值仍用 default 4 道关，仅放宽维度评分门槛。
    if cagr is not None and 0.12 <= cagr < 0.15:
        if not any(k in industry for k in _NON_GROWTH_KEYS):
            gm_avg = _gross_margin_5y_avg(fin)
            if gm_avg is not None and gm_avg >= 30:
                return "semi_growth"

    return "default"


def _gross_margin_5y_avg(fin: Optional["Financials"]) -> Optional[float]:
    if fin is None:
        return None
    gms = [p.gross_margin for p in fin.annual[:5] if p.gross_margin is not None]
    if len(gms) < 3:
        return None
    return sum(gms) / len(gms)


def _industry_rev_cagr(fin: Optional["Financials"]) -> Optional[float]:
    if fin is None:
        return None
    rev = [p.revenue for p in fin.annual[:5] if p.revenue is not None]
    if len(rev) < 2:
        return None
    latest, earliest = rev[0], rev[-1]
    if earliest <= 0 or latest <= 0:
        return None
    n = len(rev) - 1
    return (latest / earliest) ** (1 / n) - 1


def _graham_threshold(roe_avg: Optional[float]) -> float:
    """Graham PE×PB ROE 调整：max(22.5, ROE×1.5)，封顶 60。"""
    if roe_avg is None or roe_avg <= 15:
        return GRAHAM_BASE_THRESHOLD
    return min(60.0, max(GRAHAM_BASE_THRESHOLD, roe_avg * 1.5))


def compute_intrinsic_value(profile: CompanyProfile, fin: Financials, val: Valuation,
                             dividends: Optional[DividendInfo] = None) -> IntrinsicValue:
    view = classify_industry_view(profile.industry, fin)
    if view == "bank":
        iv = _iv_bank(profile, fin, val, dividends)
    elif view == "insurance":
        iv = _iv_insurance(profile, fin, val, dividends)
    elif view == "growth":
        iv = _iv_growth(profile, fin, val)
    elif view == "cyclical":
        iv = _iv_cyclical(profile, fin, val, dividends)
    else:
        # semi_growth 走 default 估值口径（FCF/Graham/OE/DCF），仅维度评分门槛不同
        iv = _iv_default(profile, fin, val)
    iv.industry_view = view
    iv.market_cap = profile.total_market_cap

    # 综合内在价值 = 多口径 fair_value 的中位数（保守视角，剔除离群值）
    fair_values = [g.fair_value for g in iv.gates if g.fair_value is not None and g.fair_value > 0]
    if fair_values and profile.total_market_cap:
        fair_values.sort()
        n = len(fair_values)
        # 中位数
        if n % 2 == 1:
            iv.fair_value = fair_values[n // 2]
        else:
            iv.fair_value = (fair_values[n // 2 - 1] + fair_values[n // 2]) / 2
        iv.discount = (iv.fair_value - profile.total_market_cap) / iv.fair_value * 100

    iv.margin_of_safety = _classify_margin_by_discount(iv.discount)
    return iv


def _classify_margin_by_discount(discount: Optional[float]) -> str:
    """按折扣率分档：
      ≥ 30%   → 充足
      10-30%  → 一般
      -10-10% → 不足
      < -10%  → 偏贵
    """
    if discount is None:
        return "未知"
    if discount >= 30:
        return "充足"
    if discount >= 10:
        return "一般"
    if discount >= -10:
        return "不足"
    return "偏贵"


# ---- 默认体系：FCF Yield / Graham / OE×12 / DCF ----

def _iv_default(profile: CompanyProfile, fin: Financials, val: Valuation) -> IntrinsicValue:
    iv = IntrinsicValue()
    shares = profile.shares
    latest = fin.latest()
    market_cap = profile.total_market_cap

    # 1. FCF Yield —— 用近 3 年平均
    fcf_yield: Optional[float] = None
    avg_fcf_total: Optional[float] = None
    fcf_series = [p.fcf_per_share for p in fin.annual[:3] if p.fcf_per_share is not None]
    if fcf_series and shares and market_cap:
        avg_fcf_per_share = sum(fcf_series) / len(fcf_series)
        avg_fcf_total = avg_fcf_per_share * shares
        fcf_yield = avg_fcf_total / market_cap * 100
    if latest and latest.fcf_per_share is not None and shares:
        latest.free_cashflow = latest.fcf_per_share * shares
    iv.gates.append(ValueGate(
        label="FCF Yield（3 年均值）≥ 6%",
        current_str=f"{fcf_yield:.2f}%" if fcf_yield is not None else "—",
        threshold_str="≥ 6%",
        passed=fcf_yield is not None and fcf_yield >= FCF_YIELD_THRESHOLD,
        # 合理市值 = FCF / 阈值收益率（即 P/FCF = 1/yield = 16.67）
        fair_value=avg_fcf_total / (FCF_YIELD_THRESHOLD / 100) if avg_fcf_total else None,
    ))

    # 2. Graham PE × PB（ROE 调整）
    graham_pe_pb: Optional[float] = None
    threshold: Optional[float] = None
    graham_fair: Optional[float] = None
    if val.pe_ttm and val.pb:
        graham_pe_pb = val.pe_ttm * val.pb
        roes = [p.roe for p in fin.annual[:5] if p.roe is not None]
        roe_avg = sum(roes) / len(roes) if roes else None
        threshold = _graham_threshold(roe_avg)
        # 合理市值 = 当前市值 × (阈值 / 当前 PE×PB)
        if graham_pe_pb > 0 and market_cap:
            graham_fair = market_cap * threshold / graham_pe_pb
    iv.gates.append(ValueGate(
        label=f"Graham PE×PB ≤ {threshold:.0f}（ROE 调整）" if threshold else "Graham PE×PB",
        current_str=f"{graham_pe_pb:.1f}" if graham_pe_pb is not None else "—",
        threshold_str=f"≤ {threshold:.0f}" if threshold else "—",
        passed=graham_pe_pb is not None and threshold is not None and graham_pe_pb <= threshold,
        fair_value=graham_fair,
    ))

    # 3. Owner Earnings × 12
    avg_oe_per_share: Optional[float] = None
    oe_value: Optional[float] = None
    if shares and fin.annual:
        oe_per_share = [p.fcf_per_share for p in fin.annual[:3] if p.fcf_per_share is not None]
        if oe_per_share:
            avg_oe_per_share = sum(oe_per_share) / len(oe_per_share)
            oe_value = avg_oe_per_share * shares * OWNER_EARNINGS_MULT
    iv.gates.append(ValueGate(
        label="OE×12（零增长）≥ 市值",
        current_str=f"{oe_value/1e8:.0f} 亿" if oe_value else "—",
        threshold_str=f"≥ 市值",
        passed=bool(oe_value and market_cap and oe_value >= market_cap),
        fair_value=oe_value,
    ))

    # 4. DCF Gordon 增长
    dcf_value: Optional[float] = None
    g_used = 0.0
    if avg_oe_per_share is not None and shares and market_cap:
        fcf_series = [p.fcf_per_share for p in fin.annual[:5] if p.fcf_per_share is not None]
        g_used = _conservative_growth(fcf_series)
        annual_oe = avg_oe_per_share * shares
        if DCF_DISCOUNT_RATE > g_used:
            dcf_value = annual_oe * (1 + g_used) / (DCF_DISCOUNT_RATE - g_used)
    iv.gates.append(ValueGate(
        label=f"DCF 含增长 ≥ 市值（g={g_used*100:.1f}%, r=8%）",
        current_str=f"{dcf_value/1e8:.0f} 亿" if dcf_value else "—",
        threshold_str=f"≥ 市值",
        passed=bool(dcf_value and market_cap and dcf_value >= market_cap),
        fair_value=dcf_value,
    ))
    return iv


def _conservative_growth(series: list[float]) -> float:
    nonneg = [v for v in series if v is not None and v > 0]
    if len(nonneg) < 2:
        return DCF_MIN_GROWTH
    latest, earliest = nonneg[0], nonneg[-1]
    n = len(nonneg) - 1
    raw_g = (latest / earliest) ** (1 / n) - 1
    return max(DCF_MIN_GROWTH, min(DCF_MAX_GROWTH, raw_g))


# ---- 成长股体系：PEG / 隐含回报 / PS÷增长率 / DCF 含增长 ----
# 参考：彼得·林奇 GARP（合理价格成长）+ 巴菲特"未来回报率"视角

GROWTH_PEG_THRESHOLD = 1.5            # PE / 净利 5 年增长率 ≤ 1.5
GROWTH_IMPLIED_RET_THRESHOLD = 8.0    # 1/PE × g 折算的年化回报 ≥ 8%
GROWTH_PS_PER_G_THRESHOLD = 0.5       # PS / 营收增长率 ≤ 0.5（销售复利视角）


def _iv_growth(profile: CompanyProfile, fin: Financials, val: Valuation) -> IntrinsicValue:
    iv = IntrinsicValue()
    market_cap = profile.total_market_cap
    pe = val.pe_ttm

    # 计算关键指标
    profit_series = [p.net_profit for p in fin.annual[:5] if p.net_profit is not None]
    profit_cagr = _cagr_pct(profit_series)
    rev_series = [p.revenue for p in fin.annual[:5] if p.revenue is not None]
    rev_cagr = _cagr_pct(rev_series)

    latest_profit = fin.annual[0].net_profit if fin.annual else None
    latest_revenue = fin.annual[0].revenue if fin.annual else None

    # 用更保守的"未来增长率" = min(历史 CAGR ÷ 2, 历史 CAGR, 10%)
    # 避免按 30%+ 历史 CAGR 算 PEG 而高估未来
    conservative_g = None
    if profit_cagr is not None:
        conservative_g = min(profit_cagr / 2, profit_cagr, 10.0) if profit_cagr > 0 else 0

    # 1. PEG ≤ 1.5
    # 关键改造：用「保守 PEG」= PE / conservative_g 来判通过，避免按 30%+ 历史 CAGR
    # 算 PEG 而过度乐观；同时报告里同时展示乐观/保守两个值
    peg_optimistic: Optional[float] = None
    peg_conservative: Optional[float] = None
    peg_fair: Optional[float] = None
    if pe and pe > 0:
        if profit_cagr and profit_cagr > 0:
            peg_optimistic = pe / profit_cagr
        if conservative_g and conservative_g > 0:
            peg_conservative = pe / conservative_g
            if latest_profit:
                peg_fair = latest_profit * (GROWTH_PEG_THRESHOLD * conservative_g)
    if peg_conservative is not None and peg_optimistic is not None:
        current_str = f"乐观 {peg_optimistic:.2f} / 保守 {peg_conservative:.2f}"
    elif peg_conservative is not None:
        current_str = f"{peg_conservative:.2f}"
    elif peg_optimistic is not None:
        current_str = f"{peg_optimistic:.2f}"
    else:
        current_str = "—"
    iv.gates.append(ValueGate(
        label="PEG ≤ 1.5（按保守 g = 历史 CAGR÷2 判定）",
        current_str=current_str,
        threshold_str="≤ 1.5",
        passed=peg_conservative is not None and peg_conservative <= GROWTH_PEG_THRESHOLD,
        fair_value=peg_fair,
    ))

    # 2. 5 年隐含年化回报 = (1/PE + g) ≥ 8% → 合理 PE = 1/(0.08 - g)
    implied: Optional[float] = None
    implied_fair: Optional[float] = None
    if pe and pe > 0 and profit_cagr is not None:
        implied = 100.0 / pe + profit_cagr
        if latest_profit and conservative_g is not None:
            g_dec = conservative_g / 100
            ret = GROWTH_IMPLIED_RET_THRESHOLD / 100
            if ret > g_dec:
                fair_pe = 1 / (ret - g_dec)
                implied_fair = latest_profit * fair_pe
    iv.gates.append(ValueGate(
        label="(1/PE + g) 隐含年化回报 ≥ 8%",
        current_str=f"{implied:.1f}%" if implied is not None else "—",
        threshold_str="≥ 8%",
        passed=implied is not None and implied >= GROWTH_IMPLIED_RET_THRESHOLD,
        fair_value=implied_fair,
    ))

    # 3. PS / 营收增长率 ≤ 0.5 → 合理 PS = 0.5 × g, 合理市值 = 营收 × 合理 PS
    ps_per_g: Optional[float] = None
    ps_fair: Optional[float] = None
    if val.ps and rev_cagr and rev_cagr > 0:
        ps_per_g = val.ps / rev_cagr
        if latest_revenue:
            conservative_rev_g = min(rev_cagr / 2, rev_cagr, 10.0)
            ps_fair = latest_revenue * (GROWTH_PS_PER_G_THRESHOLD * conservative_rev_g)
    iv.gates.append(ValueGate(
        label="PS / 营收 CAGR ≤ 0.5",
        current_str=f"{ps_per_g:.3f}" if ps_per_g is not None else "—",
        threshold_str="≤ 0.5",
        passed=ps_per_g is not None and ps_per_g <= GROWTH_PS_PER_G_THRESHOLD,
        fair_value=ps_fair,
    ))

    # 4. DCF 含增长（用保守 g）
    dcf_value: Optional[float] = None
    g_used = 0.0
    if latest_profit and latest_profit > 0 and market_cap:
        if conservative_g is not None and conservative_g > 0:
            g_used = min(conservative_g / 100, 0.10)
        if g_used >= DCF_DISCOUNT_RATE:
            g_used = DCF_DISCOUNT_RATE - 0.01
        dcf_value = latest_profit * (1 + g_used) / (DCF_DISCOUNT_RATE - g_used)
    iv.gates.append(ValueGate(
        label=f"DCF 含增长 ≥ 市值（保守 g={g_used*100:.1f}%, r=8%）",
        current_str=f"{dcf_value/1e8:.0f} 亿" if dcf_value else "—",
        threshold_str="≥ 市值",
        passed=bool(dcf_value and market_cap and dcf_value >= market_cap),
        fair_value=dcf_value,
    ))
    return iv


def _cagr_pct(series: list[float]) -> Optional[float]:
    """从最近-> 最早的序列算 CAGR，返回百分数。"""
    s = [v for v in series if v is not None]
    if len(s) < 2:
        return None
    latest, earliest = s[0], s[-1]
    if earliest <= 0 or latest <= 0:
        return None
    n = len(s) - 1
    return ((latest / earliest) ** (1 / n) - 1) * 100


# ---- 周期股体系：Shiller PE / P/B / 股息率 / 历史中位 PE ----
# Shiller CAPE 法：用过去 10 年平均 EPS 替代当前 EPS，避免周期顶部低 PE 陷阱

CYCLICAL_SHILLER_PE_THRESHOLD = 15.0   # Shiller PE ≤ 15 视为合理（周期股门槛严）
CYCLICAL_PB_THRESHOLD = 1.5            # P/B ≤ 1.5
CYCLICAL_DIV_YIELD_THRESHOLD = 4.0     # 周期股需要高分红弥补盈利波动


def _iv_cyclical(profile: CompanyProfile, fin: Financials, val: Valuation,
                  div: Optional[DividendInfo]) -> IntrinsicValue:
    """周期股估值：用 10 年平均 EPS 平滑掉周期峰谷。"""
    iv = IntrinsicValue()
    market_cap = profile.total_market_cap
    shares = profile.shares
    pe = val.pe_ttm
    pb = val.pb
    latest = fin.latest()
    roe = latest.roe if latest else None

    # 1. Shiller PE（10 年平均 EPS）≤ 15
    shiller_pe: Optional[float] = None
    shiller_fair: Optional[float] = None
    profits = [p.net_profit for p in fin.annual if p.net_profit is not None]
    if len(profits) >= 5 and shares and market_cap:
        avg_profit = sum(profits) / len(profits)
        if avg_profit > 0:
            shiller_pe = market_cap / avg_profit
            shiller_fair = avg_profit * CYCLICAL_SHILLER_PE_THRESHOLD
    iv.gates.append(ValueGate(
        label=f"Shiller PE（{len(profits)} 年均值）≤ 15",
        current_str=f"{shiller_pe:.1f}" if shiller_pe is not None else "—",
        threshold_str="≤ 15",
        passed=shiller_pe is not None and shiller_pe <= CYCLICAL_SHILLER_PE_THRESHOLD,
        fair_value=shiller_fair,
    ))

    # 2. P/B ≤ 1.5（周期股 PB 是相对稳定的锚）
    pb_fair: Optional[float] = None
    if roe and roe > 0 and latest and latest.net_profit:
        book_value = latest.net_profit / (roe / 100)
        pb_fair = book_value * CYCLICAL_PB_THRESHOLD
    iv.gates.append(ValueGate(
        label="P/B ≤ 1.5",
        current_str=f"{pb:.2f}" if pb else "—",
        threshold_str="≤ 1.5",
        passed=bool(pb and pb <= CYCLICAL_PB_THRESHOLD),
        fair_value=pb_fair,
    ))

    # 3. 股息率 ≥ 4%（周期股需现金分红弥补盈利波动）
    div_yield = _dividend_yield(profile, div, fin)
    div_fair: Optional[float] = None
    if div_yield is not None and div_yield > 0 and market_cap:
        annual_div = div_yield / 100 * market_cap
        div_fair = annual_div / (CYCLICAL_DIV_YIELD_THRESHOLD / 100)
    iv.gates.append(ValueGate(
        label="股息率 ≥ 4%（弥补周期波动）",
        current_str=f"{div_yield:.2f}%" if div_yield is not None else "—",
        threshold_str="≥ 4%",
        passed=div_yield is not None and div_yield >= CYCLICAL_DIV_YIELD_THRESHOLD,
        fair_value=div_fair,
    ))

    # 4. 当前 PE 比 Shiller PE 低 = 处于周期顶部预警；高 = 周期底部反而便宜
    # 用「PE/Shiller PE ≥ 1.0」作为通过条件（即当前 PE 不低于 10 年均值）
    cycle_ratio: Optional[float] = None
    if pe and shiller_pe and shiller_pe > 0:
        cycle_ratio = pe / shiller_pe
    iv.gates.append(ValueGate(
        label="当前 PE ≥ 10 年中位 PE（避开周期顶部）",
        current_str=f"{cycle_ratio:.2f}" if cycle_ratio is not None else "—",
        threshold_str="≥ 1.0",
        passed=cycle_ratio is not None and cycle_ratio >= 1.0,
        fair_value=None,  # 这是质量信号，不直接给市值
    ))
    return iv


# ---- 银行体系：P/B / 隐含回报 / 股息率 / 留存复利 ----

def _iv_bank(profile: CompanyProfile, fin: Financials, val: Valuation,
             div: Optional[DividendInfo]) -> IntrinsicValue:
    """巴菲特看银行的核心：低 P/B + 高 ROE + 稳定派息 = 长期复利机器。"""
    iv = IntrinsicValue()
    pb = val.pb
    market_cap = profile.total_market_cap
    latest = fin.latest()
    roe = latest.roe if latest else None

    # 公式辅助
    book_value_total: Optional[float] = None
    if roe and roe > 0 and latest and latest.net_profit:
        book_value_total = latest.net_profit / (roe / 100)

    # 1. P/B ≤ 1.5 → 合理市值 = book × 1.5
    pb_fair = book_value_total * BANK_PB_THRESHOLD if book_value_total else None
    iv.gates.append(ValueGate(
        label="P/B ≤ 1.5",
        current_str=f"{pb:.2f}" if pb else "—",
        threshold_str="≤ 1.5",
        passed=bool(pb and pb <= BANK_PB_THRESHOLD),
        fair_value=pb_fair,
    ))

    # 2. 隐含回报 ROE/P/B ≥ 10% → 合理 P/B = ROE/10. 合理市值 = book × (ROE/10)
    implied: Optional[float] = (roe / pb) if (roe and pb and pb > 0) else None
    implied_fair = (book_value_total * (roe / IMPLIED_RETURN_THRESHOLD)
                    if book_value_total and roe else None)
    iv.gates.append(ValueGate(
        label="隐含回报 ROE÷PB ≥ 10%",
        current_str=f"{implied:.1f}%" if implied is not None else "—",
        threshold_str="≥ 10%",
        passed=implied is not None and implied >= IMPLIED_RETURN_THRESHOLD,
        fair_value=implied_fair,
    ))

    # 3. 股息率 ≥ 4% → 合理市值 = 年分红 / 4%
    div_yield = _dividend_yield(profile, div, fin)
    div_fair: Optional[float] = None
    if div_yield is not None and div_yield > 0 and market_cap:
        annual_dividend = div_yield / 100 * market_cap
        div_fair = annual_dividend / (BANK_DIV_YIELD_THRESHOLD / 100)
    iv.gates.append(ValueGate(
        label="股息率 ≥ 4%",
        current_str=f"{div_yield:.2f}%" if div_yield is not None else "—",
        threshold_str="≥ 4%",
        passed=div_yield is not None and div_yield >= BANK_DIV_YIELD_THRESHOLD,
        fair_value=div_fair,
    ))

    # 4. 留存复利价值 ≥ 市值
    # 修正：假设 ROE 在 10 年内线性衰减到行业均值 10%（避免无限复利的乐观偏差）
    retained_value: Optional[float] = None
    shares = profile.shares
    if roe and roe > 0 and book_value_total and book_value_total > 0:
        retain = 0.5
        roe_decimal = roe / 100
        roe_floor = 0.10  # 衰减目标 ROE = 行业均值 10%
        # 用 10 年线性衰减 ROE 复利
        equity = book_value_total
        for year in range(10):
            current_roe = roe_decimal - (roe_decimal - roe_floor) * (year / 9)
            equity *= (1 + current_roe * retain)
        retained_value = equity
    iv.gates.append(ValueGate(
        label="留存复利价值（10 年，ROE 衰减到 10%）≥ 市值",
        current_str=f"{retained_value/1e8:.0f} 亿" if retained_value else "—",
        threshold_str=f"≥ 市值",
        passed=bool(retained_value and market_cap and retained_value >= market_cap),
        fair_value=retained_value,
    ))
    return iv


# ---- 保险体系：P/B / 股息率 / 隐含回报 / 净利稳定性 ----

def _iv_insurance(profile: CompanyProfile, fin: Financials, val: Valuation,
                   div: Optional[DividendInfo]) -> IntrinsicValue:
    iv = IntrinsicValue()
    pb = val.pb
    market_cap = profile.total_market_cap
    latest = fin.latest()
    roe = latest.roe if latest else None

    book_value_total: Optional[float] = None
    if roe and roe > 0 and latest and latest.net_profit:
        book_value_total = latest.net_profit / (roe / 100)

    # 1. P/B ≤ 1.5
    pb_fair = book_value_total * INS_PB_THRESHOLD if book_value_total else None
    iv.gates.append(ValueGate(
        label="P/B ≤ 1.5",
        current_str=f"{pb:.2f}" if pb else "—",
        threshold_str="≤ 1.5",
        passed=bool(pb and pb <= INS_PB_THRESHOLD),
        fair_value=pb_fair,
    ))

    # 2. 隐含回报 ROE/PB ≥ 10%
    implied: Optional[float] = (roe / pb) if (roe and pb and pb > 0) else None
    implied_fair = (book_value_total * (roe / IMPLIED_RETURN_THRESHOLD)
                    if book_value_total and roe else None)
    iv.gates.append(ValueGate(
        label="隐含回报 ROE÷PB ≥ 10%",
        current_str=f"{implied:.1f}%" if implied is not None else "—",
        threshold_str="≥ 10%",
        passed=implied is not None and implied >= IMPLIED_RETURN_THRESHOLD,
        fair_value=implied_fair,
    ))

    # 3. 股息率 ≥ 3%
    div_yield = _dividend_yield(profile, div, fin)
    div_fair: Optional[float] = None
    if div_yield is not None and div_yield > 0 and market_cap:
        annual_dividend = div_yield / 100 * market_cap
        div_fair = annual_dividend / (INS_DIV_YIELD_THRESHOLD / 100)
    iv.gates.append(ValueGate(
        label="股息率 ≥ 3%",
        current_str=f"{div_yield:.2f}%" if div_yield is not None else "—",
        threshold_str="≥ 3%",
        passed=div_yield is not None and div_yield >= INS_DIV_YIELD_THRESHOLD,
        fair_value=div_fair,
    ))

    # 4. 净利波动 < 40%（仅 quality 指标，不给 fair_value）
    cv: Optional[float] = None
    profits = [p.net_profit for p in fin.annual[:10] if p.net_profit is not None]
    if len(profits) >= 5:
        import statistics
        mean = sum(profits) / len(profits)
        if mean > 0:
            cv = statistics.pstdev(profits) / mean
    iv.gates.append(ValueGate(
        label="10 年净利波动 < 40%",
        current_str=f"{cv*100:.1f}%" if cv is not None else "—",
        threshold_str="< 40%",
        passed=cv is not None and cv < 0.40,
    ))
    return iv


def _dividend_yield(profile: CompanyProfile, div: Optional[DividendInfo],
                    fin: Financials) -> Optional[float]:
    """股息率 = 滚动 12 个月总分红 / 市值。

    优先用 TTM（除权日在最近 365 天内）的派息合计——避免银行/保险中期+年末分红只取最近一笔。
    回退：用 history[0]（最近一个除权年度）的合计。
    """
    if not div or not profile.current_price:
        return None
    per_10 = div.ttm_per_10_shares
    if per_10 is None and div.history:
        per_10 = div.history[0].cash_per_10_shares
    if per_10 is None:
        return None
    per_share_div = per_10 / 10
    return per_share_div / profile.current_price * 100


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------

def _news(query: str, limit: int = 10) -> list[NewsItem]:
    from stockwise.data.cache import cached_call, TTL_NEWS
    try:
        df = cached_call(
            "akshare:stock_news_em", query, TTL_NEWS,
            lambda: ak.stock_news_em(symbol=query),
        )
    except Exception:
        return []
    if df is None or df.empty:
        return []
    items: list[NewsItem] = []
    for _, r in df.head(limit).iterrows():
        items.append(NewsItem(
            title=str(r.get("新闻标题", "")).strip(),
            publish_time=str(r.get("发布时间", "")).strip(),
            source=str(r.get("文章来源", "")).strip() or None,
            summary=str(r.get("新闻内容", "")).strip()[:200] or None,
        ))
    return items


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        if isinstance(v, str) and (not v or v.strip() in {"--", "—", "nan", "None"}):
            return None
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _percentile(series: pd.Series, value: Optional[float]) -> Optional[float]:
    if value is None or series is None or series.empty:
        return None
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    return float((s <= value).mean() * 100)


def _yoy(row: Optional[pd.Series], cur: str, prev: str) -> Optional[float]:
    if row is None:
        return None
    a, b = _to_float(row.get(cur)), _to_float(row.get(prev))
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b) * 100
