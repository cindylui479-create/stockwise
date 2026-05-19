"""FastAPI 应用主体（v0.13 #57 轻量 Web UI）。

v0.13.2:
  - 修 mermaid：把 <pre><code class="language-mermaid"> 转成 <div class="mermaid"> +
    解码 HTML 实体（&quot; → "），让浏览器端 mermaid.js 正常渲染 xychart
  - 加 watch CRUD：在 watchlist 页面表单 + POST endpoints
"""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from typing import Optional

try:
    from fastapi import FastAPI, Form, HTTPException
    from fastapi.responses import HTMLResponse, RedirectResponse
except ImportError:  # pragma: no cover
    FastAPI = None

from jinja2 import Environment, FileSystemLoader, select_autoescape

from stockwise.watchlist import Watchlist


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _markdown_to_html(md: str) -> str:
    """Markdown → HTML，并把 mermaid 代码块改为浏览器可渲染的 div.mermaid。"""
    try:
        import markdown
    except ImportError:
        return f"<pre>{html_lib.escape(md)}</pre>"
    html = markdown.markdown(md, extensions=["tables", "fenced_code"])

    # mermaid 代码块：markdown 库会输出 <pre><code class="language-mermaid">...</code></pre>
    # 但 mermaid.js 需要 <div class="mermaid">原始代码</div>，且不能 HTML 实体编码
    def _fix_mermaid(m):
        inner = m.group(1)
        # 解码 HTML 实体（&quot; → " 等）
        inner = html_lib.unescape(inner)
        return f'<div class="mermaid">{inner}</div>'

    html = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        _fix_mermaid, html, flags=re.DOTALL,
    )
    return html


def _create_app():
    if FastAPI is None:
        raise RuntimeError("fastapi 未安装：pip install fastapi uvicorn")
    app = FastAPI(title="stockwise Web UI", version="0.13.2")

    @app.get("/", response_class=HTMLResponse)
    async def watchlist_page(msg: Optional[str] = None, err: Optional[str] = None):
        wl = Watchlist.load()
        holdings = [i for i in wl.items if i.buy_price and i.shares]
        total_cost = sum(i.buy_price * i.shares for i in holdings)
        tpl = _env.get_template("watchlist.html")
        return tpl.render(items=wl.items, holdings=holdings,
                           total_cost=total_cost, msg=msg, err=err)

    @app.post("/watch/add")
    async def watch_add(code: str = Form(...), market: str = Form("A"),
                         price: Optional[float] = Form(None),
                         shares: Optional[int] = Form(None)):
        code = code.strip()
        market = market.upper()
        if not code:
            return RedirectResponse("/?err=代码不能为空", status_code=303)
        wl = Watchlist.load()
        if wl.add(code, market, buy_price=price, shares=shares):
            wl.save()
            return RedirectResponse(f"/?msg=已添加 {code} ({market})", status_code=303)
        return RedirectResponse(f"/?err={code} 已存在 (用编辑而非添加)", status_code=303)

    @app.post("/watch/set/{code}")
    async def watch_set(code: str,
                         price: Optional[float] = Form(None),
                         shares: Optional[int] = Form(None)):
        wl = Watchlist.load()
        if wl.update_holding(code, buy_price=price, shares=shares):
            wl.save()
            return RedirectResponse(f"/?msg=已更新 {code}", status_code=303)
        return RedirectResponse(f"/?err=未找到 {code}", status_code=303)

    @app.post("/watch/remove/{code}")
    async def watch_remove(code: str):
        wl = Watchlist.load()
        if wl.remove(code):
            wl.save()
            return RedirectResponse(f"/?msg=已删除 {code}", status_code=303)
        return RedirectResponse(f"/?err=未找到 {code}", status_code=303)

    @app.get("/stock/{code}", response_class=HTMLResponse)
    async def stock_page(code: str):
        reports_dir = Path("reports")
        if not reports_dir.exists():
            raise HTTPException(404, "无报告目录")
        candidates = sorted(reports_dir.glob(f"{code}_*.md"), reverse=True)
        if not candidates:
            return HTMLResponse(
                f"<h1>没有 {code} 的报告</h1>"
                f"<p>请先在命令行运行 <code>python -m stockwise {code}</code> 生成报告。</p>",
                status_code=404,
            )
        md = candidates[0].read_text(encoding="utf-8")
        html_content = _markdown_to_html(md)
        tpl = _env.get_template("stock.html")
        return tpl.render(code=code, html_content=html_content,
                           file_name=candidates[0].name)

    @app.get("/portfolio", response_class=HTMLResponse)
    async def portfolio_page():
        wl = Watchlist.load()
        holdings = [i for i in wl.items if i.buy_price and i.shares]
        if not holdings:
            return HTMLResponse(
                "<h1>无持仓</h1><p>回 <a href='/'>watchlist</a> 添加买入价 / 股数。</p>"
            )
        total_cost = sum(i.buy_price * i.shares for i in holdings)
        industry_count = {}
        for h in holdings:
            ind = h.last_rating or "未知"
            industry_count[ind] = industry_count.get(ind, 0) + 1
        tpl = _env.get_template("portfolio.html")
        return tpl.render(holdings=holdings, total_cost=total_cost,
                           industry_count=industry_count)

    return app


app = _create_app() if FastAPI is not None else None
