"""启动 Web UI: python -m stockwise.web"""
from __future__ import annotations

import sys


def main():
    try:
        import uvicorn
    except ImportError:
        print("缺依赖：pip install fastapi uvicorn", file=sys.stderr)
        sys.exit(1)
    from stockwise.web.app import app
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
