from __future__ import annotations

import sys
from pathlib import Path

import click

from stockwise.analyzer.llm import LLMAnalysis, analyze
from stockwise.analyzer.scorer import score
from stockwise.config import Config
from stockwise.data.fetcher import fetch
from stockwise.data.market import parse_code
from stockwise.report.generator import render, write


@click.command()
@click.argument("code")
@click.option("--hk", is_flag=True, help="强制按港股识别（4-5 位数字代码默认为港股）")
@click.option("--no-llm", is_flag=True, help="跳过 LLM，仅用规则打分")
@click.option("--no-validate", is_flag=True, help="跳过 baostock 副源校验")
@click.option("--no-governance", is_flag=True, help="跳过 巨潮治理事件抓取")
@click.option("--no-holders", is_flag=True, help="跳过 股东结构抓取")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="报告输出目录（默认 ./reports）")
def main(code: str, hk: bool, no_llm: bool, no_validate: bool, no_governance: bool,
         no_holders: bool, out: Path | None):
    """按伯克希尔范式生成 A 股或港股的投资分析报告。

    \b
    示例:
      stockwise 600519              # 贵州茅台 (A股)
      stockwise 00700 --hk          # 腾讯控股 (港股)
      stockwise 600519 --no-llm     # 仅规则打分，不调用 LLM
    """
    cfg = Config.load(report_dir=out)

    try:
        sid = parse_code(code, hint_hk=hk)
    except ValueError as e:
        click.secho(f"错误：{e}", fg="red", err=True)
        sys.exit(2)

    click.echo(f"[1/4] 拉取 {sid.market} 市场 {sid.code} 数据 …")
    snapshot = fetch(sid, validate=not no_validate, governance=not no_governance,
                     holders=not no_holders)
    click.echo(f"      公司：{snapshot.profile.name}    当前价：{snapshot.profile.current_price}")
    v = snapshot.validation
    if not v.skipped:
        if v.error:
            click.secho(f"      副源校验失败：{v.error}（继续）", fg="yellow")
        elif v.has_warnings:
            click.secho(f"      ⚠ 副源校验：{len(v.major_diffs)} 处显著差异（>10%），详见报告", fg="yellow")
        else:
            click.echo(f"      副源校验通过（baostock 对比 {v.checked_fields} 个字段，差异 ≤10%）")

    g = snapshot.governance
    if not g.skipped:
        if g.error:
            click.secho(f"      治理事件抓取失败：{g.error}（继续）", fg="yellow")
        elif g.has_red_flags:
            click.secho(f"      ⚠ 治理：{len(g.high)} 条红旗 + {len(g.medium)} 条关注（巨潮近 180 天）", fg="yellow")
        elif g.medium:
            click.echo(f"      治理：无红旗，{len(g.medium)} 条需关注")
        else:
            click.echo(f"      治理：无重大事件（巨潮近 180 天）")

    h = snapshot.holders
    if not h.skipped and not h.error:
        if h.top_holders:
            click.echo(f"      股东结构：前 10 流通股东（{h.report_date}），见报告")
        elif h.insider_pct is not None:
            click.echo(f"      股东结构：内部人 {h.insider_pct:.1f}%，机构 {h.institution_pct or 0:.1f}%")

    click.echo(f"[2/4] 规则打分（伯克希尔范式 7 维）…")
    base_result = score(snapshot)
    click.echo(f"      初始得分 {base_result.total}/100  →  {base_result.rating}（安全边际 {base_result.margin_of_safety}）")
    if base_result.vetoes:
        click.secho(f"      ⚠ 触发一票否决：{'; '.join(base_result.vetoes)}", fg="yellow")

    llm: LLMAnalysis | None = None
    if not no_llm:
        if not cfg.llm.usable:
            click.secho(
                "警告：未配置 LLM API key（ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / OPENAI_API_KEY），"
                "跳过 LLM 解读（使用 --no-llm 可静默跳过）",
                fg="yellow", err=True,
            )
        else:
            provider_label = {
                "anthropic": "Anthropic",
                "openai": "OpenAI 兼容",
            }.get(cfg.llm.provider, cfg.llm.provider)
            endpoint = cfg.llm.base_url or "默认端点"
            click.echo(f"[3/4] 调用 {provider_label} ({cfg.llm.model} @ {endpoint}) 做定性分析 …")
            try:
                llm = analyze(snapshot, cfg.llm)
                biz = llm.business_understandability
                mgmt = llm.management_quality
                click.echo(
                    f"      LLM 业务可理解性：{biz if biz is not None else '—'}/5；"
                    f"管理层质量：{mgmt if mgmt is not None else '—'}/5"
                )
            except Exception as e:
                click.secho(f"      LLM 调用失败：{e}（继续以规则结果生成报告）", fg="yellow", err=True)
                llm = None
    else:
        click.echo("[3/4] 跳过 LLM 解读 (--no-llm)")

    click.echo(f"[4/4] 生成 Markdown 报告 …")
    report = render(snapshot, base_result, llm)
    path = write(report, sid.code, cfg.report_dir)

    # 渲染时已根据 LLM 重算分数，重新读一遍最终结果给用户看
    final = base_result
    if llm is not None:
        final = score(snapshot,
                      llm_business_score=llm.business_understandability,
                      llm_management_score=llm.management_quality)
    click.secho(
        f"\n✓ 报告已生成：{path.resolve()}\n"
        f"  最终评级：{final.rating}  得分 {final.total}/100  安全边际：{final.margin_of_safety}",
        fg="green",
    )


if __name__ == "__main__":
    main()
