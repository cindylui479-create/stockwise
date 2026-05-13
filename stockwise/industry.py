"""行业头部发现：用 baostock（独立于东财，不依赖 push2）拿 A 股全行业分类，
按净利润绝对值排序，每行业取 top N。

baostock 给的是证监会行业分类（INDUSTRYCSRC1），跟系统现有 5 类 profile 兼容。

设计：
  - baostock query_stock_industry() 拿 ~5500 只 A 股 industry → 缓存 7 天
  - 对每只股票 query_profit_data 拿最近年报净利润 → 缓存 30 天
  - 按 industry 分组，每组按 net_profit 排序，取 top N
  - 排除 ST / 退市 / 净利润缺失

multiprocessing 4 worker 并发，每 worker 独立 baostock 连接。
"""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from stockwise.data.cache import cached_call

_INDUSTRY_TTL_HOURS = 24 * 7      # 1 周
_PROFIT_TTL_HOURS = 24 * 30       # 1 月（年报数据基本不变）


@dataclass
class IndustryLeader:
    code: str                # 6 位数字
    name: str
    industry: str
    industry_code: str       # 证监会代码，如 "C36"
    net_profit: float        # 最近年报净利润
    rank_in_industry: int    # 行业内排名，1=第一
    bs_code: str             # baostock 格式如 "sh.600519"


@dataclass
class IndustryDiscovery:
    leaders: list[IndustryLeader] = field(default_factory=list)
    industry_count: int = 0
    total_scanned: int = 0
    error: Optional[str] = None


def discover_leaders(top_n: int = 3,
                     industry_filter: Optional[list[str]] = None,
                     exclude: Optional[list[str]] = None,
                     workers: int = 4,
                     progress_cb=None,
                     cache_only: bool = False) -> IndustryDiscovery:
    """发现各行业 top N。

    cache_only=True：只用已缓存的净利润数据，不再调用 baostock 拉新数据。
    用于首次扫描 timeout 后用现有缓存出结果。
    """
    industry_df = _all_industry_classifications()
    if industry_df is None or industry_df.empty:
        return IndustryDiscovery(error="baostock 行业分类拉取失败")

    # 过滤
    df = industry_df[
        ~industry_df["code_name"].str.contains("ST|退", na=False) &
        industry_df["industry"].notna() &
        (industry_df["industry"].str.strip() != "")
    ].copy()

    # 行业关键字过滤：宽松匹配，把 baostock 的 "J66货币金融服务" 拆出中文部分再匹配
    # 用户常用"银行"等口语化关键词，需 expand 到 baostock 实际类名
    if industry_filter:
        expanded = _expand_keywords(industry_filter)
        keep = df["industry"].str.contains("|".join(expanded), na=False, regex=True)
        df = df[keep]
    if exclude:
        expanded = _expand_keywords(exclude)
        drop = df["industry"].str.contains("|".join(expanded), na=False, regex=True)
        df = df[~drop]

    if df.empty:
        return IndustryDiscovery(error="过滤后无标的")

    # 拉净利润
    bs_codes = df["code"].tolist()
    if cache_only:
        profits = _fetch_profits_cache_only(bs_codes, progress_cb)
    else:
        profits = _fetch_profits_parallel(bs_codes, workers, progress_cb)
    df["net_profit"] = profits
    df = df.dropna(subset=["net_profit"])

    # 行业分组取 top N（按 net_profit 降序）
    df = df.sort_values(["industry", "net_profit"], ascending=[True, False])
    leaders_df = df.groupby("industry", sort=False).head(top_n).reset_index(drop=True)
    leaders_df["rank_in_industry"] = (
        leaders_df.groupby("industry").cumcount() + 1
    )

    leaders: list[IndustryLeader] = []
    for _, row in leaders_df.iterrows():
        bs_code = str(row["code"])
        plain_code = bs_code.split(".")[-1]
        ind_full = str(row["industry"])
        # baostock 格式: "C39计算机、通信和其他电子设备制造业"
        ind_parts = _split_industry_code(ind_full)
        leaders.append(IndustryLeader(
            code=plain_code,
            name=str(row["code_name"]),
            industry=ind_parts[1] or ind_full,
            industry_code=ind_parts[0],
            net_profit=float(row["net_profit"]),
            rank_in_industry=int(row["rank_in_industry"]),
            bs_code=bs_code,
        ))

    return IndustryDiscovery(
        leaders=leaders,
        industry_count=df["industry"].nunique(),
        total_scanned=len(df),
    )


def _all_industry_classifications() -> Optional[pd.DataFrame]:
    """拉 baostock 全部 A 股行业分类，缓存 7 天。"""
    def _call():
        import baostock as bs
        lg = bs.login()
        try:
            if lg.error_code != "0":
                return None
            rs = bs.query_stock_industry()
            return rs.get_data()
        finally:
            bs.logout()
    return cached_call("baostock:query_stock_industry", "all", _INDUSTRY_TTL_HOURS, _call)


_KEYWORD_EXPANSIONS = {
    "银行": "货币金融",
    "保险": "保险",
    "证券": "资本市场",
    "白酒": "酒、饮料",
    "饮料": "酒、饮料",
    "食品": "食品制造",
    "医药": "医药制造",
    "医疗": "卫生",
    "半导体": "计算机、通信",
    "芯片": "计算机、通信",
    "软件": "软件和信息技术",
    "互联网": "互联网和相关服务",
    "汽车": "汽车制造",
    "新能源": "电气机械",
    "家电": "电气机械",
    "煤炭": "煤炭",
    "钢铁": "黑色金属",
    "石油": "石油|油气",
    "化工": "化学原料|化学纤维",
    "电力": "电力、热力",
    "公用": "电力、热力|水的生产|燃气",
    "白色家电": "电气机械",
    "造纸": "造纸",
    "水泥": "非金属矿物",
    "航空": "航空运输",
    "运输": "运输",
    "地产": "房地产",
    "建筑": "建筑业",
    "传媒": "广播|电影|新闻",
}


def _expand_keywords(keywords: list[str]) -> list[str]:
    """口语化关键词 → baostock 实际行业关键字。未命中的原样保留。"""
    expanded = []
    for k in keywords:
        k = k.strip()
        if k in _KEYWORD_EXPANSIONS:
            expanded.append(_KEYWORD_EXPANSIONS[k])
        else:
            expanded.append(k)
    return expanded


def list_industries() -> list[tuple[str, int]]:
    """返回 baostock 所有 A 股行业及成分股数量。"""
    df = _all_industry_classifications()
    if df is None or df.empty:
        return []
    counts = df["industry"].value_counts()
    counts = counts[counts.index.notna()]
    return [(ind, int(cnt)) for ind, cnt in counts.items() if ind.strip()]


def _split_industry_code(industry: str) -> tuple[str, str]:
    """「C39计算机、通信和其他电子设备制造业」→ ("C39", "计算机、通信和其他电子设备制造业")"""
    import re
    m = re.match(r"^([A-Z]\d+)(.+)$", industry)
    if m:
        return m.group(1), m.group(2)
    return "", industry


_WORKER_LOGGED_IN = False


def _ensure_baostock_login() -> None:
    """每个 worker 进程只 login 一次。"""
    global _WORKER_LOGGED_IN
    if _WORKER_LOGGED_IN:
        return
    import baostock as bs
    bs.login()
    _WORKER_LOGGED_IN = True


def _fetch_one_profit(bs_code: str) -> Optional[float]:
    """对一只股票拿最近年报净利润，缓存 30 天。worker 必须已 login。"""
    def _call():
        _ensure_baostock_login()
        import baostock as bs
        for year in (2024, 2023, 2022):
            rs = bs.query_profit_data(code=bs_code, year=year, quarter=4)
            df = rs.get_data()
            if df is not None and not df.empty:
                val = df.iloc[0].get("netProfit")
                if val and val != "":
                    try:
                        f = float(val)
                        if f == f and abs(f) > 0:
                            return f
                    except (TypeError, ValueError):
                        continue
        return None
    try:
        return cached_call(
            "baostock:netprofit_latest", bs_code, _PROFIT_TTL_HOURS, _call,
            cache_none=True,
        )
    except Exception:
        return None


def _fetch_profits_cache_only(bs_codes: list[str], progress_cb) -> list[Optional[float]]:
    """只查缓存，不调 baostock。命中即用，未命中给 None。秒回。"""
    from stockwise.data.cache import cache_get, _NONE_SENTINEL
    results: list[Optional[float]] = []
    for i, code in enumerate(bs_codes):
        hit = cache_get("baostock:netprofit_latest", code, _PROFIT_TTL_HOURS)
        if isinstance(hit, str) and hit == _NONE_SENTINEL:
            results.append(None)
        else:
            results.append(hit)
        if progress_cb and (i + 1) % 500 == 0:
            progress_cb(i + 1, len(bs_codes))
    if progress_cb:
        progress_cb(len(bs_codes), len(bs_codes))
    return results


def _fetch_profits_parallel(bs_codes: list[str], workers: int,
                             progress_cb) -> list[Optional[float]]:
    """并发拉净利润；progress_cb(done, total) 用于实时进度。"""
    results: list[Optional[float]] = [None] * len(bs_codes)
    if workers <= 1:
        for i, c in enumerate(bs_codes):
            results[i] = _fetch_one_profit(c)
            if progress_cb:
                progress_cb(i + 1, len(bs_codes))
        return results

    with mp.Pool(workers, initializer=_ensure_baostock_login) as pool:
        for i, val in enumerate(pool.imap(_fetch_one_profit, bs_codes, chunksize=20)):
            results[i] = val
            if progress_cb and (i + 1) % 50 == 0:
                progress_cb(i + 1, len(bs_codes))
    if progress_cb:
        progress_cb(len(bs_codes), len(bs_codes))
    return results
