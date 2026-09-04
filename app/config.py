"""配置管理：config.yaml（源开关/限流参数）+ .env（敏感项）."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# .env 中的敏感项（cookie / api key），通过 os.environ 读取
SENSITIVE_KEYS = [
    "BILI_SESSDATA",
    "YOUTUBE_API_KEY",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "PODCASTINDEX_API_KEY",
    "PODCASTINDEX_API_SECRET",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class ProviderConfig(BaseModel):
    enabled: bool = False
    base_url: str | None = None       # pansou 等自托管服务地址
    limit: int = 0                    # 每源返回条数上限；0 = 使用全局 per_source_limit


class ProbeConfig(BaseModel):
    per_domain_concurrency: int = 2
    global_concurrency: int = 10
    baidu_min_interval: float = 2.0   # 百度 share 页最小间隔(秒)
    timeout_connect: float = 3.0
    timeout_read: float = 8.0
    cache_ttl: int = 3600             # 探测结果缓存 TTL(秒)
    auto_probe_top: int = 3           # 搜索时每类型自动探测条数


class SearchConfig(BaseModel):
    deadline: float = 8.0             # 单源整体超时(秒)
    per_source_limit: int = 100       # 每源返回条数上限
    enrich_top: int = 10              # 每类型 enrich 的条数


class AppConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    probe: ProbeConfig = Field(default_factory=ProbeConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    proxy: str | None = None          # 可选 http/socks5 代理(启用国际源时配置)


# 默认配置：Phase 0 实测结论 —— 中文源直连可用，国际源默认关闭(需代理)
DEFAULT_CONFIG: dict = {
    "providers": {
        "bilibili": {"enabled": True},
        "netease": {"enabled": True},
        "douban": {"enabled": True},
        "pansou": {"enabled": True, "base_url": "http://127.0.0.1:8888"},
        "internet_archive": {"enabled": False},
        "openlibrary": {"enabled": False},
        "gutenberg": {"enabled": False},
        "librivox": {"enabled": False},
        "ximalaya": {"enabled": False},
        "youtube": {"enabled": False},
        "spotify": {"enabled": False},
        "podcastindex": {"enabled": False},
        "weread": {"enabled": False},
    },
    "probe": {},
    "search": {},
}


@lru_cache(maxsize=1)
def load_config(path: str | Path | None = None) -> AppConfig:
    """加载配置：config.yaml 不存在时用内置默认值."""
    load_dotenv(PROJECT_ROOT / ".env")

    cfg_path = Path(path) if path else CONFIG_PATH
    data: dict = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    # 深度合并：yaml 覆盖默认
    merged = {
        **DEFAULT_CONFIG,
        **data,
        "providers": {
            **DEFAULT_CONFIG["providers"],
            **(data.get("providers") or {}),
        },
        "probe": {**DEFAULT_CONFIG["probe"], **(data.get("probe") or {})},
        "search": {**DEFAULT_CONFIG["search"], **(data.get("search") or {})},
    }
    if data.get("proxy"):
        merged["proxy"] = data["proxy"]

    return AppConfig.model_validate(merged)


def get_secret(key: str) -> str | None:
    """读取 .env / 环境变量中的敏感项."""
    return os.environ.get(key)


def config_reload() -> None:
    """config.yaml 热加载（改配置后无需重启的入口）."""
    load_config.cache_clear()
