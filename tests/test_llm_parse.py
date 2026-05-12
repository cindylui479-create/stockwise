"""LLM 输出解析测试（不调用真实 API）。"""
from stockwise.analyzer.llm import _parse


SAMPLE = """<main_business>
某公司主营 X 业务，主要营收来自 Y。
</main_business>

<moat_analysis>
公司在 X 领域具备品牌护城河，毛利率长期高于同行 20 个百分点，定价权显著。
转换成本中等。综合判断：护城河强。
</moat_analysis>

<business_understandability>4</business_understandability>
<understandability_note>消费品业务模式简单，10 年后仍可预测。</understandability_note>

<management_quality>4</management_quality>
<management_note>资本配置理性，分红稳定，无重大违规。</management_note>

<inversion>
1) 行业被新一代产品颠覆——可能性低；
2) 监管反转——可能性中；
3) 品牌污点——可能性低。
</inversion>

<intrinsic_value_view>
当前 PE 在历史中位附近，相对内在价值偏贵。
</intrinsic_value_view>

<verdict>
优质企业但当前价格偏贵，进入 watchlist 等待回调。
</verdict>
"""


def test_parse_full_response():
    r = _parse(SAMPLE)
    assert "X 业务" in r.main_business
    assert "护城河强" in r.moat_analysis
    assert r.business_understandability == 4
    assert "消费品" in r.understandability_note
    assert r.management_quality == 4
    assert "无重大违规" in r.management_note
    assert "监管反转" in r.inversion
    assert "watchlist" in r.verdict


def test_parse_score_clamped():
    r = _parse("<business_understandability>99</business_understandability>")
    assert r.business_understandability == 5

    r = _parse("<management_quality>not a number</management_quality>")
    assert r.management_quality is None


def test_parse_handles_missing_tags():
    r = _parse("nothing here")
    assert r.main_business == ""
    assert r.moat_analysis == ""
    assert r.business_understandability is None
