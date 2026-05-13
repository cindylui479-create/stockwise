"""轻量级数据缓存：SQLite + pickle。

- 缓存键：`(namespace, key)`，namespace 如 "akshare:stock_financial_abstract"
- TTL 按 namespace 分层（财报 7 天、估值 1 天、新闻 4 小时）
- 缓存默认存 `~/.stockwise/cache.db`
- 通过环境变量 `STOCKWISE_NO_CACHE=1` 完全禁用

API:
    cache_get(namespace, key, ttl_hours)  → Any or None
    cache_set(namespace, key, value)
    cached_call(namespace, key, ttl_hours, fn, *args, **kwargs)
        若 cache 命中且未过期，返回 cache；否则 fn(*args, **kwargs) 并 cache。
"""
from __future__ import annotations

import os
import pickle
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

_DB_PATH = Path.home() / ".stockwise" / "cache.db"
_DEFAULT_TTL_HOURS = 24

_ENABLED: Optional[bool] = None
_CONN: Optional[sqlite3.Connection] = None


def _enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.environ.get("STOCKWISE_NO_CACHE", "").strip() not in ("1", "true", "yes")
    return _ENABLED


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONN = sqlite3.connect(str(_DB_PATH))
        _CONN.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value BLOB NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (namespace, key)
            )
        """)
        _CONN.commit()
    return _CONN


def cache_get(namespace: str, key: str, ttl_hours: float = _DEFAULT_TTL_HOURS) -> Any:
    if not _enabled():
        return None
    try:
        cur = _conn().execute(
            "SELECT value, created_at FROM cache WHERE namespace=? AND key=?",
            (namespace, key),
        )
        row = cur.fetchone()
        if row is None:
            return None
        value_blob, created_at = row
        age_hours = (time.time() - created_at) / 3600
        if age_hours > ttl_hours:
            return None
        return pickle.loads(value_blob)
    except Exception:
        return None


def cache_set(namespace: str, key: str, value: Any) -> None:
    if not _enabled():
        return
    try:
        blob = pickle.dumps(value)
        _conn().execute(
            "REPLACE INTO cache (namespace, key, value, created_at) VALUES (?, ?, ?, ?)",
            (namespace, key, blob, time.time()),
        )
        _conn().commit()
    except Exception:
        pass


_NONE_SENTINEL = "__cached_none__"


def cached_call(namespace: str, key: str, ttl_hours: float,
                fn: Callable, *args,
                cache_none: bool = False, **kwargs) -> Any:
    """带缓存包装：命中即返回；否则执行并写入。

    cache_none=True：把 None 结果也缓存（哨兵值），避免反复重试已知失败的调用
    （如停牌股的 baostock profit 查询，每次都返回空但要 5 秒）
    """
    hit = cache_get(namespace, key, ttl_hours)
    if isinstance(hit, str) and hit == _NONE_SENTINEL:
        return None
    if hit is not None:
        return hit
    try:
        result = fn(*args, **kwargs)
    except Exception:
        result = None
    if result is not None:
        cache_set(namespace, key, result)
    elif cache_none:
        cache_set(namespace, key, _NONE_SENTINEL)
    return result


def clear_cache(namespace: Optional[str] = None) -> int:
    """清空缓存（指定 namespace 或全部）。返回删除条数。"""
    if not _enabled():
        return 0
    if namespace:
        cur = _conn().execute("DELETE FROM cache WHERE namespace=?", (namespace,))
    else:
        cur = _conn().execute("DELETE FROM cache")
    _conn().commit()
    return cur.rowcount


# TTL 推荐（按数据更新频率）
TTL_PROFILE = 24 * 7        # 公司基本信息 1 周
TTL_FINANCIALS = 24 * 3     # 财报数据 3 天（季报披露后才变）
TTL_VALUATION = 6           # 估值数据 6 小时
TTL_DIVIDENDS = 24 * 7      # 分红历史 1 周
TTL_NEWS = 4                # 新闻 4 小时
TTL_GOVERNANCE = 24         # 治理事件 1 天
TTL_HOLDERS = 24 * 7        # 季度披露，1 周即可
