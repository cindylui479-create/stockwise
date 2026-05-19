"""FastAPI 应用主体（v0.13 #57 轻量 Web UI）。"""
from __future__ import annotations

from pathlib import Path

try:
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover
    FastAPI = None

from jinja2 import Environment, FileSystemLoader, select_autoescape

from stockwise.watchlist import Watchlist


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _create_app():
    if FastAPI is None:
        raise RuntimeError("fastapi 未安装：pip install fastapi uvicorn")
    app = FastAPI(title="stockwise Web UI", version="0.13.0")

    @app.get("/", response_class=HTMLResponse)
    async def watchlist_page():
        wl = Watchlist.load()
        # 计算简单统计
        holdings = [i for i in wl.items if i.buy_price and i.shares]
        total_cost = sum(i.buy_price * i.shares for i in holdings)
        tpl = _env.get_template("watchlist.html")
        return tpl.render(items=wl.items, holdings=holdings, total_cost=total_cost)

    @app.get("/stock/{code}", response_class=HTMLResponse)
    async def stock_page(code: str):
        # 读取最新报告（按文件命名约定 reports/CODE_*_YYYY-MM-DD.md）
        from datetime import date
        reports_dir = Path("reports")
        if not reports_dir.exists():
            raise HTTPException(404, "无报告目录")
        # 找匹配的最近一个 md
        candidates = sorted(reports_dir.glob(f"{code}_*.md"), reverse=True)
        if not candidates:
            return HTMLResponse(
                f"<h1>没有 {code} 的报告</h1>"
                f"<p>请先在命令行运行 <code>python -m stockwise {code}</code> 生成报告。</p>",
                status_code=404,
            )
        md = candidates[0].read_text(encoding="utf-8")
        # 简单 Markdown → HTML（用 mistune 或 markdown）
        try:
            import markdown
            html = markdown.markdown(md, extensions=["tables", "fenced_code"])
        except ImportError:
            html = f"<pre>{md}</pre>"
        tpl = _env.get_template("stock.html")
        return tpl.render(code=code, html_content=html, file_name=candidates[0].name)

    @app.get("/portfolio", response_class=HTMLResponse)
    async def portfolio_page():
        wl = Watchlist.load()
        holdings = [i for i in wl.items if i.buy_price and i.shares]
        if not holdings:
            return HTMLResponse(
                "<h1>无持仓</h1><p>用 <code>stockwise watch add CODE --price X --shares Y</code> 添加。</p>"
            )
        total_cost = sum(i.buy_price * i.shares for i in holdings)
        # 行业分布（简化：从 last 状态推断）
        industry_count = {}
        for h in holdings:
            ind = h.last_rating or "未知"  # 简化版只看评级
            industry_count[ind] = industry_count.get(ind, 0) + 1
        tpl = _env.get_template("portfolio.html")
        return tpl.render(holdings=holdings, total_cost=total_cost,
                           industry_count=industry_count)

    return app


app = _create_app() if FastAPI is not None else None
