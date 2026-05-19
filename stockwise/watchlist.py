"""个人 watchlist：~/.stockwise/watchlist.json 维护股票列表。

数据格式（JSON）：
{
  "items": [
    {"code": "600519", "market": "A", "name": "贵州茅台", "added_at": "2026-05-12T10:00:00",
     "last_rating": "质量好但有瑕疵", "last_score": 79, "last_action": "..."}
  ]
}
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

_WATCH_PATH = Path.home() / ".stockwise" / "watchlist.json"


@dataclass
class WatchItem:
    code: str
    market: str                  # "A" or "HK"
    name: Optional[str] = None
    added_at: Optional[str] = None
    last_rating: Optional[str] = None
    last_score: Optional[int] = None
    last_action: Optional[str] = None
    last_margin: Optional[str] = None
    last_run: Optional[str] = None
    # v0.10：个人持仓信息
    buy_price: Optional[float] = None    # 加权买入价
    shares: Optional[int] = None         # 持有股数

    def current_pnl(self, current_price: Optional[float]) -> Optional[dict]:
        """计算浮盈浮亏。返回 dict 含 cost / market_value / pnl_amount / pnl_pct 或 None。"""
        if self.buy_price is None or self.shares is None or current_price is None:
            return None
        cost = self.buy_price * self.shares
        mkt = current_price * self.shares
        return {
            "cost": cost,
            "market_value": mkt,
            "pnl_amount": mkt - cost,
            "pnl_pct": (current_price - self.buy_price) / self.buy_price * 100,
        }


@dataclass
class Watchlist:
    items: list[WatchItem] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Watchlist":
        if not _WATCH_PATH.exists():
            return cls()
        try:
            raw = json.loads(_WATCH_PATH.read_text(encoding="utf-8"))
            return cls(items=[WatchItem(**item) for item in raw.get("items", [])])
        except Exception:
            return cls()

    def save(self) -> None:
        _WATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"items": [asdict(i) for i in self.items]}
        _WATCH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, code: str, market: str, name: Optional[str] = None,
            buy_price: Optional[float] = None, shares: Optional[int] = None) -> bool:
        if any(i.code == code and i.market == market for i in self.items):
            return False
        self.items.append(WatchItem(
            code=code, market=market, name=name,
            added_at=datetime.now().isoformat(timespec="seconds"),
            buy_price=buy_price, shares=shares,
        ))
        return True

    def update_holding(self, code: str, *, buy_price: Optional[float] = None,
                       shares: Optional[int] = None) -> bool:
        """更新已有持仓的买入价 / 股数（None 表示不修改）。"""
        for i in self.items:
            if i.code == code:
                if buy_price is not None:
                    i.buy_price = buy_price
                if shares is not None:
                    i.shares = shares
                return True
        return False

    def remove(self, code: str) -> bool:
        before = len(self.items)
        self.items = [i for i in self.items if i.code != code]
        return len(self.items) != before

    def update_result(self, code: str, *, rating: str, score: int, action: str,
                      margin: str, name: Optional[str] = None) -> None:
        for i in self.items:
            if i.code == code:
                i.last_rating = rating
                i.last_score = score
                i.last_action = action
                i.last_margin = margin
                if name:
                    i.name = name
                i.last_run = datetime.now().isoformat(timespec="seconds")
                return
