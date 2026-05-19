"""评级-margin 自洽测试（v0.7 修复：避免边界跳变）。"""
from __future__ import annotations

from stockwise.analyzer.scorer import _rating


def test_high_score_strong_discount_long_term_hold():
    assert _rating(90, 30.0, []) == "值得长期持有"


def test_high_score_mild_discount_合理_estimation():
    assert _rating(88, 5.0, []) == "优质合理估值"


def test_high_score_overpriced():
    assert _rating(90, -15.0, []) == "优质但偏贵"


def test_mid_score_positive_discount_reasonable():
    assert _rating(75, 1.0, []) == "质量好且估值合理"


def test_mid_score_boundary_plus_zero():
    """v0.7 关键修复：discount=0 时 70-84 分应得"质量好且估值合理"，而非"有瑕疵"。"""
    assert _rating(75, 0.0, []) == "质量好且估值合理"


def test_mid_score_negative_discount_flawed():
    assert _rating(75, -5.0, []) == "质量好但有瑕疵"


def test_no_discount_data_treated_as_zero():
    assert _rating(75, None, []) == "质量好且估值合理"


def test_below_70_未达_standard():
    assert _rating(60, 20.0, []) == "未达伯克希尔标准"


def test_veto_overrides_all():
    assert _rating(95, 50.0, ["净利亏损"]) == "避免"
    assert _rating(40, -50.0, ["商誉过半"]) == "避免"


def test_boundary_85_threshold():
    # 84 vs 85 分 boundary
    assert _rating(84, 30.0, []) == "质量好且估值合理"
    assert _rating(85, 30.0, []) == "值得长期持有"


def test_泸州老窖_regression():
    """v0.7 修复回归测试：82 分 + 折价 +10%（卡在原 '不足/一般' 边界）应归"质量好且估值合理"。"""
    # 注：泸州老窖实测 discount ~ +10%，按原 4 档 enum 会因 +9.8% 被归"不足"导致评级降档
    assert _rating(82, 10.0, []) == "质量好且估值合理"
    assert _rating(82, 9.8, []) == "质量好且估值合理"  # 边界附近不抖
    assert _rating(82, -0.1, []) == "质量好但有瑕疵"   # 一过 0 才降档
