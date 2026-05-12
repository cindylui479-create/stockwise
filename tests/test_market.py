import pytest

from stockwise.data.market import parse_code


def test_a_share_main_board():
    sid = parse_code("600519")
    assert sid.code == "600519"
    assert sid.market == "A"


def test_a_share_chinext():
    sid = parse_code("300750")
    assert sid.code == "300750"
    assert sid.market == "A"


def test_hk_explicit_flag():
    sid = parse_code("700", hint_hk=True)
    assert sid.code == "00700"
    assert sid.market == "HK"


def test_hk_4_digit_inferred():
    sid = parse_code("0700")
    assert sid.code == "00700"
    assert sid.market == "HK"


def test_hk_dot_suffix():
    sid = parse_code("00700.HK")
    assert sid.code == "00700"
    assert sid.market == "HK"


def test_invalid_letters():
    with pytest.raises(ValueError):
        parse_code("AAPL")


def test_invalid_a_share_prefix():
    with pytest.raises(ValueError):
        parse_code("999999")
