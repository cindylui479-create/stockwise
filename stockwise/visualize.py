"""screen 结果的可视化：行业 × 标的 二维 HTML 热图。

输出独立 HTML（无外部依赖），按 quick_score 着色。
打开浏览器即可看，方便分享。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def render_heatmap(results: list, out_path: Path, title: str = "stockwise 行业筛选热图") -> Path:
    """生成 industry × rank 二维热图（HTML）。

    results: QuickResult 对象列表
    """
    # 按行业分组
    by_industry: dict[str, list] = {}
    for r in results:
        by_industry.setdefault(r.industry, []).append(r)

    # 行业按行业内最高分降序
    industries_sorted = sorted(
        by_industry.items(),
        key=lambda kv: max((s.quick_score for s in kv[1]), default=0),
        reverse=True,
    )

    rows = []
    for industry, items in industries_sorted:
        items_sorted = sorted(items, key=lambda x: x.industry_rank)
        max_rank = max((s.industry_rank for s in items_sorted), default=1)
        cells = []
        for rank in range(1, max_rank + 1):
            cell = next((x for x in items_sorted if x.industry_rank == rank), None)
            if cell is None:
                cells.append('<td class="empty">—</td>')
            else:
                color = _score_color(cell.quick_score)
                pe_str = f"PE {cell.pe:.1f}" if cell.pe else ""
                roe_str = f"ROE {cell.roe_5y:.0f}%" if cell.roe_5y else ""
                cells.append(
                    f'<td style="background:{color}">'
                    f'<div class="code">{cell.code}</div>'
                    f'<div class="name">{cell.name}</div>'
                    f'<div class="score">{cell.quick_score}/30</div>'
                    f'<div class="meta">{pe_str} ｜ {roe_str}</div>'
                    f'</td>'
                )
        # 补齐到最大列数
        while len(cells) < 3:
            cells.append('<td class="empty">—</td>')
        rows.append(f'<tr><th class="industry">{industry[:18]}</th>{"".join(cells)}</tr>')

    html = f"""<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8"><title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 20px; background: #f5f5f7; }}
h1 {{ font-size: 20px; }}
table {{ border-collapse: collapse; width: 100%; max-width: 1100px; background: white; }}
th, td {{ border: 1px solid #e0e0e0; padding: 8px; text-align: left; vertical-align: top; }}
th.industry {{ width: 22%; background: #fafafa; font-size: 13px; }}
td {{ width: 26%; font-size: 12px; }}
.code {{ font-weight: 600; }}
.name {{ color: #444; font-size: 11px; }}
.score {{ font-weight: 700; margin-top: 4px; }}
.meta {{ color: #666; font-size: 10px; margin-top: 2px; }}
.empty {{ color: #bbb; text-align: center; background: #fafafa; }}
.legend {{ margin-top: 16px; font-size: 12px; color: #666; }}
.legend span {{ padding: 2px 8px; margin-right: 6px; border-radius: 3px; }}
</style></head><body>
<h1>{title}</h1>
<p style="color:#666; font-size:13px">每行业 top N 按净利润排序；颜色按 quick_score (0-30)。</p>
<table>
<thead><tr><th>行业</th><th>#1 头部</th><th>#2 次席</th><th>#3 第三</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table>
<p class="legend">
评分梯度：
<span style="background:{_score_color(28)}">25-30 卓越</span>
<span style="background:{_score_color(22)}">20-24 良好</span>
<span style="background:{_score_color(17)}">15-19 一般</span>
<span style="background:{_score_color(10)}">&lt;15 差</span>
</p>
</body></html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _score_color(score: int) -> str:
    """0-30 → 颜色（红→黄→绿渐变）。"""
    if score >= 25:
        return "#a5d6a7"  # 深绿
    if score >= 22:
        return "#c8e6c9"  # 浅绿
    if score >= 18:
        return "#fff9c4"  # 浅黄
    if score >= 15:
        return "#ffe0b2"  # 浅橙
    if score >= 10:
        return "#ffccbc"  # 浅红
    return "#ef9a9a"  # 红
