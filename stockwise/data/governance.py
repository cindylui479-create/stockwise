"""治理事件抓取：从巨潮（cninfo）拉个股公告，按关键词分级。

设计：
  - 抓最近 180 天公告（A 股）
  - 按公告类型 + 标题关键词分级为 high / medium / low
  - high：监管立案、处罚、问询、商誉减值、诉讼裁决、控股股东失信、股权冻结
  - medium：关联交易（大金额）、对外担保、大额质押、高管减持
  - low：常规关联交易披露、ESG 报告、日常运营
  - 过滤掉年报/季报/股东会通知等纯例行公告

港股不支持（巨潮覆盖度低；港股治理事件应该走 HKEx 披露易）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import akshare as ak

from stockwise.data.models import GovernanceEvent, GovernanceReport

LOOKBACK_DAYS = 180

# 公告类型 → 严重度。键是 akshare 给的「公告类型」字段。
_CATEGORY_SEVERITY = {
    "诉讼仲裁": "high",
    "立案调查": "high",
    "监管处罚": "high",
    "问询函": "high",
    "商誉减值": "high",
    "失信": "high",
    "股权冻结": "high",
    "重大资产重组": "medium",
    "关联交易": "medium",
    "对外担保": "medium",
    "股权质押": "medium",
    "重要股东减持": "medium",
    "高管变动": "medium",
    "股权激励": "low",
    "回购进展情况": "low",
    "ESG公告": "low",
}

# 标题关键词补充识别（覆盖 category 为「其他」但实际敏感的公告）
_TITLE_HIGH_KEYS = [
    "立案", "处罚", "违规", "问询函", "警示函", "涉嫌",
    "失联", "失信", "冻结", "强制执行", "判决", "裁定",
    "诉讼进展",
]
_TITLE_MEDIUM_KEYS = [
    "减持", "质押", "担保", "关联交易", "高管辞职", "董事辞职",
]


def fetch_events(code: str, market: str) -> GovernanceReport:
    """A 股走巨潮公告；港股 (v0.9) 走东财港股新闻分级。"""
    if market != "A":
        # v0.9：港股治理事件路由到 hk_governance 模块（东财港股新闻为源）
        from stockwise.data.hk_governance import fetch_events as hk_fetch
        return hk_fetch(code)

    from stockwise.data.cache import cached_call, TTL_GOVERNANCE
    end = datetime.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    try:
        df = cached_call(
            "cninfo:notice_report", code, TTL_GOVERNANCE,
            lambda: ak.stock_individual_notice_report(
                security=code, symbol="全部",
                begin_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            ),
        )
    except Exception as e:
        return GovernanceReport(error=f"巨潮接口失败：{type(e).__name__}: {e}")

    if df is None or df.empty:
        return GovernanceReport()

    events: list[GovernanceEvent] = []
    for _, row in df.iterrows():
        category = str(row.get("公告类型", "")).strip()
        title = str(row.get("公告标题", "")).strip()
        date = str(row.get("公告日期", "")).strip()
        url = row.get("网址")
        severity = _classify(category, title)
        if severity is None:
            continue
        events.append(GovernanceEvent(
            date=date, title=title, category=category or "其他",
            severity=severity, url=str(url) if url else None,
        ))
    return GovernanceReport(events=events)


def _classify(category: str, title: str) -> Optional[str]:
    """返回 high / medium / low / None（忽略）。

    顺序：
      1. 审计/程序性公告（无论 category）→ 忽略
      2. category 表精确匹配
      3. 「重组」类 category → medium
      4. 标题里的减值类
      5. 高/中关键词
    """
    # 1. 审计或纯程序文档：不是事件
    if any(k in title for k in ("减值测试", "减值审核", "测试情况", "测试审核",
                                  "审核报告", "核查意见", "专项说明", "评估报告")):
        return None

    # 2. category 表
    if category in _CATEGORY_SEVERITY:
        return _CATEGORY_SEVERITY[category]

    # 3. 重组类
    if "重组" in category:
        return "medium"

    # 4. 减值实质性事件
    if "减值" in title:
        if any(k in title for k in ("减值损失", "计提", "重大减值", "巨额")):
            return "high"
        return "medium"

    # 5. 标题关键词
    for kw in _TITLE_HIGH_KEYS:
        if kw in title:
            return "high"
    for kw in _TITLE_MEDIUM_KEYS:
        if kw in title:
            return "medium"
    return None
