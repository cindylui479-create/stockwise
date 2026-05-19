"""港股治理事件抓取（v0.9）。

数据源：东财港股公司新闻接口 `stock_news_em`（symbol=4 位港股代码）。
非 HKEx 披露易官方公告，但东财抓取已覆盖大部分重要事件（回购 / 监管 / 业绩 / 重组）。
分级逻辑复用 A 股 governance 的标题关键词识别。

港股的限制：
  - 关联交易披露不如 A 股巨潮规范；
  - 监管处罚一般在香港证监会 (SFC) 发布，东财可能延迟；
  - 内幕消息 (Inside Information) 公告会出现在标题中。
报告会标注数据源「东财港股新闻」，提醒用户重要决策应核对 HKEx 披露易原始公告。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import akshare as ak

from stockwise.data.models import GovernanceEvent, GovernanceReport

LOOKBACK_DAYS = 180

# 港股关键词覆盖：标题里常用繁体 / 英文 / 港式说法
_TITLE_HIGH_KEYS = [
    # 监管 / 司法
    "监管", "監管", "处罚", "處罰", "违规", "違規", "调查", "調查", "立案",
    "诉讼", "訴訟", "judgment", "judgement", "litigation", "investigation",
    "失信", "冻结", "凍結", "judgment", "SFC", "证监会", "證監會",
    "Inside Information", "內幕消息", "内幕消息",
    "暫停買賣", "暂停买卖", "delisting", "退市",
    "重大不利", "盈警", "profit warning",
    "重述", "restatement", "审计", "審計",
]
_TITLE_MEDIUM_KEYS = [
    "关联", "關聯", "connected", "related party",
    "质押", "質押", "pledge",
    "担保", "擔保", "guarantee",
    "减持", "減持", "disposal", "shareholder reduction",
    "高管辞职", "辞任", "辭任", "resignation",
    "重组", "重組", "restructuring",
    "更改", "变更", "變更",
    "资本重组", "供股", "rights issue",
    "回购", "回購", "share buyback", "repurchase",  # medium 不是 high
]
# 例行 / 噪音过滤
_TITLE_IGNORE_KEYS = [
    "业绩电话会", "業績電話會", "earnings call",
    "業績預告", "业绩预告",
    "投资者交流", "投資者交流",
    "评级", "評級", "目标价", "目標價",  # 第三方分析师评级
    "下跌", "上涨", "上漲",  # 价格波动新闻
    "成交", "收盘", "收盤",
]


def fetch_events(code: str) -> GovernanceReport:
    """港股治理事件：东财港股新闻 → 关键词分级。

    code 是 4 位港股代码（如 '00700'）。
    """
    from stockwise.data.cache import cached_call, TTL_GOVERNANCE
    try:
        df = cached_call(
            "em:hk_stock_news", code, TTL_GOVERNANCE,
            lambda: ak.stock_news_em(symbol=code),
        )
    except Exception as e:
        return GovernanceReport(error=f"东财港股新闻接口失败：{type(e).__name__}: {e}")

    if df is None or df.empty:
        return GovernanceReport()

    cutoff = datetime.today() - timedelta(days=LOOKBACK_DAYS)
    events: list[GovernanceEvent] = []
    for _, row in df.iterrows():
        title = str(row.get("新闻标题", "")).strip()
        publish_time = str(row.get("发布时间", "")).strip()
        if not title:
            continue
        # 时间过滤
        try:
            dt = datetime.strptime(publish_time[:10], "%Y-%m-%d")
            if dt < cutoff:
                continue
        except Exception:
            pass

        severity = _classify(title)
        if severity is None:
            continue

        events.append(GovernanceEvent(
            date=publish_time[:10],
            title=title,
            category="港股公司新闻",
            severity=severity,
            url=str(row.get("新闻链接", "")) or None,
        ))
    return GovernanceReport(events=events)


def _classify(title: str) -> Optional[str]:
    """返回 high / medium / low / None。"""
    # 1. 例行噪音过滤
    if any(k in title for k in _TITLE_IGNORE_KEYS):
        return None

    # 2. 高严重度关键词
    for kw in _TITLE_HIGH_KEYS:
        if kw in title:
            return "high"

    # 3. 中等严重度
    for kw in _TITLE_MEDIUM_KEYS:
        if kw in title:
            return "medium"

    return None
