"""启动 Web UI: python -m stockwise.web [--port 8001] [--host 0.0.0.0]"""
from __future__ import annotations

import argparse
import sys


def main():
    try:
        import uvicorn
    except ImportError:
        print("缺依赖：pip install fastapi uvicorn", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="stockwise Web UI")
    parser.add_argument("--port", type=int, default=8001,
                        help="端口（默认 8001 避开常见 8000 占用）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1；用 0.0.0.0 暴露给局域网）")
    args = parser.parse_args()

    from stockwise.web.app import app
    print(f"\n  ✨ stockwise Web UI 启动中 http://{args.host}:{args.port}/\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
