"""行业周期位置（v0.9）。

数据源：同花顺行业指数 5 年收盘价分位。

为什么用价格分位而不是 PE 分位：
  - 同花顺行业指数接口只给 OHLCV，无 PE
  - 巨潮行业 PE 接口在 akshare 1.18 损坏（columns mismatch）
  - 价格分位作为周期位置 proxy 已足够（高位 ≈ 估值贵 + 业绩好，底部 ≈ 估值便宜 + 业绩差）

用途：白酒/煤炭/航空等强周期行业，识别"核心资产→价值陷阱"风险（高位）
      或"周期底部反转"机会（底部）。

输出：
  - percentile（0-100，当前价 / 5 年区间）
  - label（高位 ≥80 / 中位偏高 50-80 / 中位 20-50 / 底部 ≤20）
  - low_5y / high_5y / current
  - lookback_days 实际数据长度
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class IndustryCycle:
    industry: str
    ths_name: Optional[str] = None       # 实际命中的同花顺行业名
    current: Optional[float] = None
    low_5y: Optional[float] = None
    high_5y: Optional[float] = None
    percentile: Optional[float] = None    # 0-100
    label: str = "未知"
    skipped: bool = False
    error: Optional[str] = None


# stockwise INDUSTRYCSRC1 字段 → 同花顺行业指数名（精确对齐 ak.stock_board_industry_name_ths 列表）
# 优先覆盖"明显周期性 + 高 ROE 消费"两类（这两类最需要周期定位）
_INDUSTRY_MAP = {
    # 消费 — 估值周期性强
    "白酒": "白酒",
    "酒、饮料": "白酒",
    "饮料制造": "饮料制造",
    "食品": "食品加工制造",
    # 周期 / 资源
    "煤炭": "煤炭开采加工",
    "煤炭开采": "煤炭开采加工",
    "钢铁": "钢铁",
    "有色金属": "工业金属",
    "石油": "油气开采及服务",
    "化工": "化学制品",
    "化学制品": "化学制品",
    "化学原料": "化学原料",
    "水泥": "建筑材料",
    "航运": "港口航运",
    "航空运输": "机场航运",
    # 房地产 / 建材
    "房地产": "房地产",
    "建材": "建筑材料",
    # 制造 / 周期
    "汽车": "汽车整车",
    "汽车整车": "汽车整车",
    "汽车零部件": "汽车零部件",
    "家电": "白色家电",
    "家用电器": "白色家电",
    # 医药 — 政策周期
    "医药": "化学制药",
    "化学制药": "化学制药",
    "中药": "中药",
    "医疗器械": "医疗器械",
    "生物制品": "生物制品",
    # 金融
    "银行": "银行",
    "货币金融服务": "银行",
    "保险": "保险",
    "证券": "证券",
    # 电力 / 公用
    "电力": "电力",
    "燃气": "燃气",
}


def _resolve_ths_name(industry: Optional[str]) -> Optional[str]:
    """从 INDUSTRYCSRC1 行业名映射到同花顺行业指数名。"""
    if not industry:
        return None
    # 精确匹配
    if industry in _INDUSTRY_MAP:
        return _INDUSTRY_MAP[industry]
    # 关键词包含匹配
    for key, ths in _INDUSTRY_MAP.items():
        if key in industry:
            return ths
    return None


def fetch_industry_cycle(industry: Optional[str]) -> IndustryCycle:
    """对给定行业拉 5 年指数收盘价分位。

    无映射 / 接口失败时返回 skipped=True，不影响主报告。
    """
    cycle = IndustryCycle(industry=industry or "")
    ths_name = _resolve_ths_name(industry)
    if not ths_name:
        cycle.skipped = True
        cycle.error = "未在周期映射表中（仅周期/消费/医药/金融启用）"
        return cycle
    cycle.ths_name = ths_name

    from stockwise.data.cache import cached_call, TTL_VALUATION
    try:
        df = cached_call(
            "ths:industry_index", ths_name, TTL_VALUATION,
            lambda: _ths_fetch(ths_name),
        )
    except Exception as e:
        cycle.error = f"同花顺行业指数接口失败：{type(e).__name__}: {e}"
        return cycle

    if df is None or df.empty:
        cycle.error = "无数据"
        return cycle

    try:
        closes = df["收盘价"].dropna().astype(float)
    except Exception as e:
        cycle.error = f"解析失败：{e}"
        return cycle
    if len(closes) < 100:
        cycle.error = f"数据点太少（{len(closes)} 条），分位不可信"
        return cycle

    current = float(closes.iloc[-1])
    low = float(closes.min())
    high = float(closes.max())
    cycle.current = current
    cycle.low_5y = low
    cycle.high_5y = high
    if high <= low:
        cycle.percentile = 50.0
    else:
        cycle.percentile = (current - low) / (high - low) * 100
    cycle.label = _classify_position(cycle.percentile)
    return cycle


def _ths_fetch(ths_name: str):
    import akshare as ak
    start = (datetime.today() - timedelta(days=365 * 5)).strftime("%Y%m%d")
    end = datetime.today().strftime("%Y%m%d")
    return ak.stock_board_industry_index_ths(symbol=ths_name,
                                              start_date=start, end_date=end)


def _classify_position(percentile: Optional[float]) -> str:
    if percentile is None:
        return "未知"
    if percentile >= 80:
        return "高位"
    if percentile >= 50:
        return "中位偏高"
    if percentile >= 20:
        return "中位"
    return "底部"
