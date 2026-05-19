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


# INDUSTRYCSRC1（akshare 细分名）→ baostock 证监会大类关键词（包含匹配）
_BAOSTOCK_INDUSTRY_MAP = {
    # 食品 / 酒
    "白酒": "酒、饮料",
    "饮料": "酒、饮料",
    "酒、饮料": "酒、饮料",
    "食品制造": "食品制造",
    "食品": "食品制造",
    "农副食品": "农副食品加工",
    # 资源 / 周期
    "煤炭": "煤炭开采",
    "钢铁": "黑色金属",
    "黑色金属": "黑色金属",
    "有色金属": "有色金属",
    "石油": "石油",
    "化工": "化学原料",
    "化学制品": "化学原料",
    "化学原料": "化学原料",
    # 制造
    "汽车": "汽车制造",
    "家电": "电气机械",
    "家用电器": "电气机械",
    "电气机械": "电气机械",
    "计算机、通信": "计算机、通信",       # 制造业-计算机、通信和其他电子设备制造业
    "计算机": "计算机、通信",
    "通用设备": "通用设备",
    "专用设备": "专用设备",
    # 医药
    "化学制药": "医药制造",
    "中药": "医药制造",
    "生物制品": "医药制造",
    "医药": "医药制造",
    # 金融
    "银行": "货币金融",
    "货币金融": "货币金融",
    "保险": "保险",
    "证券": "资本市场",
    "资本市场": "资本市场",
    # 房地产
    "房地产": "房地产",
    # 公用 / 能源
    "电力": "电力",
    "燃气": "燃气",
    # 信息技术
    "软件和信息技术": "软件和信息技术服务",
    "软件": "软件和信息技术服务",
    "互联网": "互联网和相关服务",
    "电信": "电信",
    # 商业 / 文娱
    "新闻和出版": "新闻和出版",
    "广播、电视、电影": "广播",
    "教育": "教育",
    # 交通
    "铁路运输": "铁路运输",
    "道路运输": "道路运输",
    "航空运输": "航空运输",
    "水上运输": "水上运输",
    # 农林牧渔
    "农业": "农业",
    "畜牧": "畜牧",
    # 建筑
    "土木工程建筑": "土木工程建筑",
    "建筑装饰": "建筑装饰",
    "房屋建筑": "房屋建筑",
    # 文教 / 家具
    "家具": "家具",
    "造纸": "造纸",
    "文教": "文教",
    "纺织": "纺织",
    "皮革": "皮革",
    # 非金属
    "非金属矿物": "非金属矿物",
    "水泥": "非金属矿物",
}


def _map_to_baostock_industry(industry: str) -> Optional[str]:
    """INDUSTRYCSRC1 细分名 → baostock 证监会大类的关键词（用于 contains 匹配）。

    匹配策略：
      1. 精确名匹配（_BAOSTOCK_INDUSTRY_MAP）
      2. 关键字包含匹配（"金融业-货币金融服务" 含 "货币金融"）
      3. 提取 INDUSTRYCSRC1 末段子行业作为 baostock 关键词回退
         （"金融业-保险业" → "保险业"；用于未在映射表里的行业）
    """
    if not industry:
        return None
    if industry in _BAOSTOCK_INDUSTRY_MAP:
        return _BAOSTOCK_INDUSTRY_MAP[industry]
    for key, mapped in _BAOSTOCK_INDUSTRY_MAP.items():
        if key in industry:
            return mapped
    # 回退：末段子行业（去掉 "II" 等后缀）
    parts = industry.split("-")
    if len(parts) > 1:
        tail = parts[-1].strip().rstrip("II").rstrip("Ⅱ")
        # 至少 2 字以上的关键字才用做回退（避免太宽泛）
        if len(tail) >= 3:
            return tail[:6]  # 取前 6 字作为 baostock 关键字
    return None


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

    baostock industry 字段是证监会大类格式（如 'C15酒、饮料和精制茶制造业'），
    与 INDUSTRYCSRC1 的细分名（如"白酒"）不直接匹配。
    映射策略：用关键词匹配 INDUSTRYCSRC1 → baostock 大类，覆盖主要行业。
    """
    from stockwise.industry import _ensure_baostock_login
    import baostock as bs
    _ensure_baostock_login()

    rs = bs.query_stock_industry()
    df = rs.get_data()
    if df is None or df.empty:
        return []

    # INDUSTRYCSRC1 → baostock 大类关键词映射（取 baostock industry 字符串需含的关键字）
    bs_keyword = _map_to_baostock_industry(industry)
    if bs_keyword:
        members = df[df["industry"].str.contains(bs_keyword, na=False, regex=False)]
    else:
        # fallback：用 INDUSTRYCSRC1 头 2 字
        members = df[df["industry"].str.contains(industry[:2], na=False, regex=False)] \
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
