"""轻量 Web UI（v0.13 #57）。

启动：
    python -m stockwise.web

访问：
    http://127.0.0.1:8000/           # watchlist 表
    http://127.0.0.1:8000/portfolio  # 持仓视角
    http://127.0.0.1:8000/stock/600519  # 单股报告（Markdown 渲染）

依赖：fastapi + uvicorn（需手动 pip install）。
"""
