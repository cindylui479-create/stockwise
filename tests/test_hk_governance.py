"""港股治理事件分级测试。"""
from __future__ import annotations

from stockwise.data.hk_governance import _classify


def test_high_regulatory_investigation():
    assert _classify("某港股遭證監會立案調查") == "high"
    assert _classify("ABC Ltd announces investigation by SFC") == "high"


def test_high_litigation():
    assert _classify("XYZ Holdings 诉讼裁决：败诉支付罚款") == "high"


def test_high_profit_warning():
    assert _classify("某公司发布盈警 預計 2025 年虧損擴大") == "high"


def test_high_trading_suspension():
    assert _classify("某公司暫停買賣公告") == "high"


def test_high_inside_information():
    assert _classify("Inside Information: 重大不利消息披露") == "high"


def test_medium_buyback():
    """回购是 medium——管理层认为股价低估的信号，但也可能反映"无更好用途"，提醒关注。"""
    assert _classify("腾讯控股 5月18日回购 5亿港元") == "medium"


def test_medium_connected_transaction():
    assert _classify("關聯交易：附屬公司向控股股東出售資產") == "medium"


def test_medium_shareholder_reduction():
    assert _classify("主要股東減持公告") == "medium"


def test_ignore_earnings_call():
    assert _classify("腾讯控股 2026年第一季度业绩电话会") is None


def test_ignore_price_movement():
    assert _classify("某公司股价上涨 5% 创近期新高") is None


def test_ignore_analyst_rating():
    assert _classify("摩根大通给予 ABC 公司目標價 上調至 ...") is None


def test_normal_news_no_signal():
    assert _classify("公司参展 AI 大会推出新产品") is None
