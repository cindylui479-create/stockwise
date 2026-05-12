"""__main__：路由到 cli.cli。

兼容两种用法：
  python -m stockwise 600519           # 自动识别为 analyze 子命令
  python -m stockwise analyze 600519
  python -m stockwise watch add 600519
"""
import re
import sys

from stockwise.cli import cli

_STOCK_CODE_RE = re.compile(r"^\d{4,6}(\.HK)?$", re.IGNORECASE)

if __name__ == "__main__":
    # 若首个位置参数像股票代码，自动插入 "analyze"（保留旧用法）
    if len(sys.argv) >= 2 and _STOCK_CODE_RE.match(sys.argv[1]):
        sys.argv.insert(1, "analyze")
    cli()
