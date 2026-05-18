"""调用 LLM，按伯克希尔范式产出定性分析。

输出 schema（XML 标签包裹，标签外不要文字）：
  <main_business>...</main_business>
  <moat_analysis>...</moat_analysis>
  <business_understandability>0-5 整数</business_understandability>
  <understandability_note>...</understandability_note>
  <management_quality>0-5 整数</management_quality>
  <management_note>...</management_note>
  <inversion>...</inversion>           ← Munger 反向思考：什么会让这家公司死
  <intrinsic_value_view>...</intrinsic_value_view>
  <verdict>...</verdict>

provider 分发同前：anthropic / openai。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from stockwise.config import LLMConfig
from stockwise.data.models import StockSnapshot

SYSTEM_PROMPT = """你是一位以巴菲特-芒格价值投资框架为准则的资深分析师。
你不追求短期题材，不预测下个季度业绩，只关心：

  1. 这是不是一门"可以理解、十年后仍可预测"的好生意？
  2. 它是否具备真正的、可持续的护城河？
  3. 管理层是否诚信能干，过去的资本配置是否理性？
  4. 当前价格是否给出了足够的安全边际？

针对用户提供的一只股票，输出以下章节，全部使用中文，每节用 XML 标签包裹：

<main_business>1 段，60-120 字，说明公司主业、最主要的盈利来源、客户/产品定位。</main_business>

<moat_analysis>2-3 段，约 200-400 字。从以下维度论证护城河（不要复述财务数字，要解释"为什么"）：
- 品牌/定价权（消费者愿意为它支付溢价吗？）
- 转换成本（客户离开它的代价有多高？）
- 网络效应 / 规模成本优势 / 牌照壁垒 / 专利
- 是否在过去 5-10 年扩大了护城河？
明确说"护城河强 / 一般 / 弱"。</moat_analysis>

<business_understandability>整数 0-5。
5 = 业务模式极简单，10 年后仍可预测（如可口可乐、自来水）。
3 = 中等可预测性。
0 = 高度复杂、技术快速变化、监管不稳定。</business_understandability>
<understandability_note>1-2 句，说明给这个分的依据。</understandability_note>

<management_quality>整数 0-5。
依据：(a) 资本配置历史（分红/回购/再投资节奏）；(b) 是否有过度并购、稀释股本、或财务激进；
(c) 公开信息（特别是「治理事件」输入字段中的红旗事件）中是否有违规、舞弊、关联交易疑虑；
(d) 股东沟通的坦诚程度。
若有 high 级别红旗事件（监管立案/处罚/重大诉讼/失信），最多给 2 分；
若仅有 medium 级别（关联交易/质押/担保），给 3-4 分；
若无任何治理事件且资本配置理性，可给 5 分。</management_quality>
<management_note>1-2 句，说明给这个分的依据，以及任何治理瑕疵。</management_note>

<inversion>2-3 段，约 200-300 字。芒格式反向思考：
"什么样的情形会让这家公司在 5-10 年内严重受损甚至消亡？"
列举 3-5 个可能的致命场景（行业被颠覆、监管反转、品牌污点、关键人物风险、资产负债表恶化等），
评估每个场景的可能性（高/中/低）。这是"避免明显的坏"。</inversion>

<intrinsic_value_view>1-2 段。基于公司当前的盈利能力和增长前景，给出对内在价值的定性判断：
当前股价相对于内在价值是"明显低估 / 接近合理 / 偏贵 / 明显高估"？
不要给具体目标价，但说清楚以"什么样的盈利水平"折算多少倍 PE/PFCF 是合理的。</intrinsic_value_view>

<verdict>2-3 句话。综合上述判断，按伯克希尔范式给出一句话结论。
注意：巴菲特从不说"卖出"。可能的措辞：
- "值得长期持有"
- "优质企业但当前价格偏贵，进入 watchlist 等待回调"
- "质量尚可但护城河不深，更适合谨慎观察"
- "不在能力圈内 / 跳过"
- "存在明显风险，避免"</verdict>

注意：
- 不要给短期目标价或买卖时点；
- 信息不足直说"公开信息有限"；
- 严格使用上述 XML 标签，标签外不要有任何文字。
"""


@dataclass
class LLMAnalysis:
    main_business: str = ""
    moat_analysis: str = ""
    business_understandability: Optional[int] = None
    understandability_note: str = ""
    management_quality: Optional[int] = None
    management_note: str = ""
    inversion: str = ""
    intrinsic_value_view: str = ""
    verdict: str = ""
    raw: str = ""


def analyze(snapshot: StockSnapshot, llm: LLMConfig) -> LLMAnalysis:
    if not llm.usable:
        raise RuntimeError("LLM api_key / auth_token 均未配置")
    user_payload = _build_user_payload(snapshot)
    if llm.provider == "anthropic":
        text = _call_anthropic(llm, user_payload)
    elif llm.provider == "openai":
        text = _call_openai(llm, user_payload)
    else:
        raise ValueError(f"未知 provider: {llm.provider}")
    return _parse(text)


# ---------------------------------------------------------------------------
# Provider 实现
# ---------------------------------------------------------------------------

def _call_anthropic(llm: LLMConfig, user_payload: str) -> str:
    import anthropic

    kwargs: dict = {}
    if llm.auth_token:
        kwargs["auth_token"] = llm.auth_token
        kwargs["api_key"] = None
    elif llm.api_key:
        kwargs["api_key"] = llm.api_key
    if llm.base_url:
        kwargs["base_url"] = llm.base_url
    kwargs["http_client"] = _http_client_kwargs(llm)
    # max_retries=1 防 SDK 默认 2 次重试 × 120s 把 subprocess 顶过 timeout
    kwargs["max_retries"] = 1
    client = anthropic.Anthropic(**kwargs)

    response = client.messages.create(
        model=llm.model,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_payload}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


def _call_openai(llm: LLMConfig, user_payload: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(
            "使用 openai 兼容 provider 需要安装 openai 包：pip install openai"
        ) from e

    kwargs: dict = {"api_key": llm.api_key}
    if llm.base_url:
        kwargs["base_url"] = llm.base_url
    kwargs["http_client"] = _http_client_kwargs(llm)
    kwargs["max_retries"] = 1
    client = OpenAI(**kwargs)

    response = client.chat.completions.create(
        model=llm.model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
    )
    return response.choices[0].message.content or ""


def _http_client_kwargs(llm: LLMConfig):
    # 始终返回带 timeout 的 httpx.Client，避免 SDK 默认无限等待把 subprocess 拖死
    import httpx
    return httpx.Client(
        verify=not llm.insecure_ssl,
        trust_env=llm.trust_env,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
    )


# ---------------------------------------------------------------------------
# payload 构造
# ---------------------------------------------------------------------------

def _holders_payload(snap: StockSnapshot):
    h = snap.holders
    if h.skipped or h.error:
        return "数据缺失"
    if h.top_holders:
        return {
            "数据源": h.source,
            "报告期": h.report_date,
            "前 10 流通股东": [
                f"#{i+1} {r.name}（{r.nature or '—'}）持 {r.pct:.2f}% — {r.change or '不变'}"
                + (f"（{r.change_pct:+.1f}%）" if r.change_pct is not None else "")
                for i, r in enumerate(h.top_holders)
            ],
        }
    return {
        "数据源": h.source,
        "内部人持股": f"{h.insider_pct:.1f}%" if h.insider_pct is not None else "—",
        "机构持股": f"{h.institution_pct:.1f}%" if h.institution_pct is not None else "—",
        "机构数量": h.institution_count,
        "注": "yfinance 港股机构数据偏美股 13F，本地基金覆盖弱",
    }


def _build_user_payload(snap: StockSnapshot) -> str:
    p = snap.profile
    iv = snap.intrinsic
    payload = {
        "公司": {
            "代码": p.code,
            "市场": "A 股" if p.market == "A" else "港股",
            "简称": p.name,
            "行业": p.industry,
            "上市日期": p.listing_date,
            "当前价": p.current_price,
            "货币": p.currency,
            "总市值": p.total_market_cap,
            "总股本": p.shares,
        },
        "近 10 年关键财务（最近在前）": [
            {
                "报告期": fp.period,
                "营收": fp.revenue,
                "净利润": fp.net_profit,
                "ROE(%)": fp.roe,
                "毛利率(%)": fp.gross_margin,
                "净利率(%)": fp.net_margin,
                "资产负债率(%)": fp.debt_ratio,
                "经营现金流": fp.operating_cashflow,
                "每股FCF": fp.fcf_per_share,
                "FCF总额": fp.free_cashflow,
                "营收同比(%)": fp.revenue_yoy,
                "净利同比(%)": fp.profit_yoy,
            }
            for fp in snap.financials.annual
        ],
        "估值": {
            "PE(TTM)": snap.valuation.pe_ttm,
            "PB": snap.valuation.pb,
            "PS": snap.valuation.ps,
        },
        "内在价值估算": {
            "评估口径": iv.industry_view,
            "市值": iv.market_cap,
            "通过/总数": f"{iv.passes_count()}/{iv.total_gates()}",
            "安全边际": iv.margin_of_safety,
            "各道关": [
                {"关口": g.label, "当前": g.current_str, "阈值": g.threshold_str, "通过": g.passed}
                for g in iv.gates
            ],
        },
        "分红记录": [
            {"年份": r.year, "每10股派息": r.cash_per_10_shares}
            for r in snap.dividends.history[:15]
        ],
        "连续派息年数": snap.dividends.consecutive_years,
        "近期新闻标题": [f"[{n.publish_time}] {n.title}" for n in snap.news],
        "股东结构 & 持仓变动": _holders_payload(snap),
        "治理事件（巨潮披露，近 180 天）": {
            "红旗事件（high）": [f"[{e.date}] {e.category} | {e.title}" for e in snap.governance.high],
            "需要关注（medium）": [f"[{e.date}] {e.category} | {e.title}" for e in snap.governance.medium],
            "已忽略例行公告类型": "年报/季报/股东会通知等不进入此清单",
        } if snap.governance.events else "无重大治理事件（或数据缺失）",
    }
    return (
        "请按伯克希尔范式分析以下公司：\n\n```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        + "\n```"
    )


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL)


def _parse(text: str) -> LLMAnalysis:
    out = LLMAnalysis(raw=text)
    # 合并同名标签：LLM 偶尔会把一节拆成多个 <foo>...</foo>，旧版 dict 推导只保留最后一段
    sections: dict[str, list[str]] = {}
    for m in _TAG_RE.finditer(text):
        sections.setdefault(m.group(1), []).append(m.group(2).strip())
    merged = {k: "\n\n".join(v) for k, v in sections.items()}

    out.main_business = merged.get("main_business", "")
    out.moat_analysis = merged.get("moat_analysis", "")
    out.understandability_note = merged.get("understandability_note", "")
    out.management_note = merged.get("management_note", "")
    out.inversion = merged.get("inversion", "")
    out.intrinsic_value_view = merged.get("intrinsic_value_view", "")
    out.verdict = merged.get("verdict", "")
    out.business_understandability = _clamp_int(merged.get("business_understandability"), 0, 5)
    out.management_quality = _clamp_int(merged.get("management_quality"), 0, 5)
    return out


def _clamp_int(raw: Optional[str], lo: int, hi: int) -> Optional[int]:
    if not raw:
        return None
    m = re.search(r"-?\d+", raw)
    if not m:
        return None
    try:
        return max(lo, min(hi, int(m.group())))
    except ValueError:
        return None
