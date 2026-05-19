"""行业 ROE 横截面分位（v0.11 #51）。

价值：识别"周期顶部高 ROE 假象"——如陕煤当前 ROE 高，但同行业其他煤企也都高，
说明这是行业 cyclical peak，非个体阿尔法。

数据：baostock query_stock_industry → 取同行业成员 → query_profit_data 拉 ROE。
为速度只取最新一年 + 限制最多 30 个对标，按市值加权过滤。
缓存 24h（行业排名变化慢）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IndustryRoeRank:
    industry: str
    company_roe: Optional[float] = None
    peers_sample: int = 0           # 实际取样的同行数
    peers_avg_roe: Optional[float] = None
    peers_median_roe: Optional[float] = None
    company_rank: Optional[int] = None   # 1 表示最高
    percentile: Optional[float] = None   # 0-100，分位（100 = 行业第一）
    skipped: bool = False
    error: Optional[str] = None
    peer_codes: list[str] = field(default_factory=list)


# 仅对 5 类 profile 启用（其他行业无需横向比较）
_ENABLED_VIEWS = {"default", "bank", "insurance", "cyclical", "semi_growth", "growth"}


def fetch_industry_roe_rank(code: str, industry: Optional[str],
                              company_roe: Optional[float]) -> IndustryRoeRank:
    """对 code 计算其在行业内的 ROE 分位。

    industry 为 INDUSTRYCSRC1 行业名（如"煤炭开采"）。
    company_roe 为当前公司近 5 年 ROE 均值（避免单一年波动）。
    """
    out = IndustryRoeRank(industry=industry or "", company_roe=company_roe)
    if not industry or company_roe is None:
        out.skipped = True
        out.error = "缺行业或 ROE 数据"
        return out

    from stockwise.data.cache import cached_call, TTL_FINANCIALS
    try:
        peers = cached_call(
            "baostock:industry_peers_roe", industry, TTL_FINANCIALS,
            lambda: _peers_roe(industry),
        )
    except Exception as e:
        out.error = f"拉取同行业失败：{type(e).__name__}: {e}"
        return out

    # 过滤无效 + 自己
    valid = [(c, r) for c, r in peers if c != code and r is not None and -50 < r < 100]
    if len(valid) < 5:
        out.skipped = True
        out.error = f"同行业有效样本不足 5 只（实际 {len(valid)}）"
        return out

    roes = sorted([r for _, r in valid], reverse=True)
    out.peers_sample = len(roes)
    out.peers_avg_roe = sum(roes) / len(roes)
    n = len(roes)
    out.peers_median_roe = roes[n // 2] if n % 2 == 1 else (roes[n // 2 - 1] + roes[n // 2]) / 2
    out.peer_codes = [c for c, _ in valid]

    # 排名：company 比多少 peer 高？
    better_than = sum(1 for r in roes if company_roe > r)
    out.company_rank = n - better_than + 1   # 1 = top
    out.percentile = better_than / n * 100   # 越高越好
    return out


def _peers_roe(industry: str) -> list[tuple[str, Optional[float]]]:
    """从 baostock 拉同行业成员的近 5 年 ROE 均值。

    简化：先拉行业表（按 INDUSTRYCSRC1 字段匹配），取前 30 个代码（baostock 行业代码无市值），
    然后对每只查 profit_data 取近 5 年 ROE 均值。
    """
    from stockwise.industry import _ensure_baostock_login
    import baostock as bs
    _ensure_baostock_login()

    # 同行业成员
    rs = bs.query_stock_industry()
    df = rs.get_data()
    if df is None or df.empty:
        return []
    # baostock industry 字段是简化名（如"采掘业"），需要关键词包含匹配
    members = df[df["industry"].str.contains(industry[:2], na=False)] \
              if len(industry) >= 2 else df[df["industry"] == industry]
    members = members.head(30)

    peers: list[tuple[str, Optional[float]]] = []
    from datetime import datetime
    latest_year = datetime.now().year - 1   # 去年年报
    for _, row in members.iterrows():
        bs_code = row["code"]
        plain_code = bs_code.split(".")[-1] if "." in bs_code else bs_code
        roes: list[float] = []
        for y in range(latest_year - 4, latest_year + 1):
            try:
                rs2 = bs.query_profit_data(code=bs_code, year=y, quarter=4)
                pdf = rs2.get_data()
                if pdf is None or pdf.empty:
                    continue
                v = pdf.iloc[0].get("roeAvg")
                if v is None or v == "":
                    continue
                roes.append(float(v) * 100)  # baostock 是 0-1 比例
            except Exception:
                continue
        avg = sum(roes) / len(roes) if roes else None
        peers.append((plain_code, avg))
    return peers
