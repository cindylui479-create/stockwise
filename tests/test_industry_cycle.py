"""行业周期位置：纯函数测试（不打网络）。"""
from __future__ import annotations

from stockwise.data.industry_cycle import _classify_position, _resolve_ths_name


def test_classify_top():
    assert _classify_position(85) == "高位"
    assert _classify_position(100) == "高位"


def test_classify_upper_mid():
    assert _classify_position(60) == "中位偏高"
    assert _classify_position(79.9) == "中位偏高"


def test_classify_mid():
    assert _classify_position(35) == "中位"


def test_classify_bottom():
    assert _classify_position(10) == "底部"
    assert _classify_position(0) == "底部"


def test_classify_none():
    assert _classify_position(None) == "未知"


# ---- _resolve_ths_name ----

def test_resolve_exact_match():
    assert _resolve_ths_name("白酒") == "白酒"
    assert _resolve_ths_name("煤炭开采") == "煤炭开采加工"


def test_resolve_keyword_match():
    """长行业名含关键词时模糊匹配。"""
    assert _resolve_ths_name("酒、饮料和精制茶制造业") == "白酒"
    assert _resolve_ths_name("煤炭开采和洗选业") == "煤炭开采加工"


def test_resolve_no_match():
    assert _resolve_ths_name("批发零售") is None
    assert _resolve_ths_name(None) is None


def test_resolve_consumer_categories():
    assert _resolve_ths_name("家电") == "白色家电"
    assert _resolve_ths_name("家用电器制造") == "白色家电"


def test_resolve_financial():
    assert _resolve_ths_name("银行") == "银行"
    assert _resolve_ths_name("保险") == "保险"
