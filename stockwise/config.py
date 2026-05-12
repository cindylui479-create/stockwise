from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

load_dotenv()

Provider = Literal["anthropic", "openai"]

# 各 provider 的默认模型
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "deepseek-chat",
}


@dataclass(frozen=True)
class ScoreWeights:
    financial: int = 30
    valuation: int = 30
    industry: int = 20
    health: int = 20

    def total(self) -> int:
        return self.financial + self.valuation + self.industry + self.health


@dataclass(frozen=True)
class RatingThresholds:
    strong_buy: int = 80
    buy: int = 65
    hold: int = 40


@dataclass(frozen=True)
class LLMConfig:
    """LLM 调用所需的全部参数。

    provider:
      - "anthropic": 用 anthropic SDK，可配合 ANTHROPIC_BASE_URL 走代理转发；
                     api_key 走 x-api-key 头；auth_token 走 Authorization: Bearer
                     （Claude Code 风格，多见于第三方代理）
      - "openai":   用 openai SDK，兼容 DeepSeek / Kimi / GLM 等 OpenAI 协议端点

    insecure_ssl: True 时跳过 TLS 证书校验（自签证书代理需要）。
    trust_env:    False 时忽略系统 HTTP/SOCKS 代理（直连代理 IP 时需要）。
    """
    provider: Provider
    api_key: Optional[str]
    auth_token: Optional[str]
    base_url: Optional[str]
    model: str
    insecure_ssl: bool = False
    trust_env: bool = True

    @property
    def usable(self) -> bool:
        return bool(self.api_key or self.auth_token)


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    weights: ScoreWeights
    thresholds: RatingThresholds
    report_dir: Path

    # 兼容老代码：cli.py 早先访问 cfg.api_key / cfg.model
    @property
    def api_key(self) -> Optional[str]:
        return self.llm.api_key

    @property
    def model(self) -> str:
        return self.llm.model

    @classmethod
    def load(cls, report_dir: Path | None = None) -> "Config":
        return cls(
            llm=_load_llm(),
            weights=ScoreWeights(),
            thresholds=RatingThresholds(),
            report_dir=report_dir or Path("reports"),
        )


def _load_llm() -> LLMConfig:
    """从环境变量推导 provider / key / base_url / model。

    优先级：
      1. STOCKWISE_PROVIDER 显式指定
      2. 否则按 key 存在性自动判断（OPENAI_API_KEY 优先于 ANTHROPIC_API_KEY 当显式声明 openai 时）
      3. 都没有则默认 anthropic
    """
    provider_raw = os.environ.get("STOCKWISE_PROVIDER", "").strip().lower()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or None
    anthropic_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or None
    openai_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("STOCKWISE_API_KEY")
        or None
    )

    if provider_raw in ("anthropic", "openai"):
        provider: Provider = provider_raw  # type: ignore[assignment]
    elif openai_key and not (anthropic_key or anthropic_token):
        provider = "openai"
    else:
        provider = "anthropic"

    auth_token: Optional[str] = None
    if provider == "anthropic":
        api_key = anthropic_key
        auth_token = anthropic_token
        base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
    else:
        api_key = openai_key
        base_url = (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("STOCKWISE_BASE_URL")
            or None
        )

    model = os.environ.get("STOCKWISE_MODEL") or DEFAULT_MODELS[provider]

    insecure_ssl = _parse_bool(os.environ.get("STOCKWISE_INSECURE_SSL"), default=False)
    trust_env = _parse_bool(os.environ.get("STOCKWISE_TRUST_ENV"), default=True)

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        auth_token=auth_token,
        base_url=base_url,
        model=model,
        insecure_ssl=insecure_ssl,
        trust_env=trust_env,
    )


def _parse_bool(raw: Optional[str], default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
