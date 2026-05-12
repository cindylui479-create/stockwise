from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Market = Literal["A", "HK"]


@dataclass(frozen=True)
class StockId:
    code: str          # 规范化代码：A 股 6 位（"600519"）；港股 5 位（"00700"）
    market: Market

    @property
    def display(self) -> str:
        return f"{self.code}.{'SH' if self.code.startswith(('600','601','603','605','688','689','900')) else 'SZ' if self.market == 'A' else 'HK'}"


_A_PREFIX = ("600", "601", "603", "605", "688", "689", "000", "001", "002", "003", "300", "301", "900")


def parse_code(raw: str, hint_hk: bool = False) -> StockId:
    """根据输入判断 A 股 / 港股，并规范化代码。

    规则：
    - 含 .HK 后缀，或 hint_hk=True，或纯数字长度为 4-5 → 港股，补 0 至 5 位
    - 6 位且前缀属于 A 股范围 → A 股
    - 否则报错
    """
    s = raw.strip().upper()
    if s.endswith(".HK") or s.startswith("HK"):
        digits = re.sub(r"\D", "", s)
        return StockId(code=digits.zfill(5)[-5:], market="HK")

    if not s.isdigit():
        raise ValueError(f"无法识别的股票代码：{raw!r}（仅支持 A 股 6 位代码或港股 4-5 位代码）")

    if hint_hk or (1 <= len(s) <= 5 and len(s) != 6):
        return StockId(code=s.zfill(5), market="HK")

    if len(s) == 6 and s.startswith(_A_PREFIX):
        return StockId(code=s, market="A")

    raise ValueError(f"无法识别的股票代码：{raw!r}（A 股需 6 位且前缀正确，港股请加 --hk 或 .HK 后缀）")
