"""Provider 注册表：名称 → 类的显式映射."""

from __future__ import annotations

import logging

from httpx import AsyncClient

from app.config import AppConfig, ProviderConfig
from app.models import ProviderInfo, ResourceType
from app.providers.base import BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# 显式注册：新增源在这里加一行
PROVIDERS: dict[str, type[BaseProvider]] = {}

# 延迟导入避免循环依赖，保持注册表集中
def _register_all() -> None:
    if PROVIDERS:
        return
    from app.providers.bilibili import BilibiliProvider
    from app.providers.douban import DoubanProvider
    from app.providers.internet_archive import InternetArchiveProvider
    from app.providers.netease import NeteaseProvider
    from app.providers.openlibrary import OpenLibraryProvider
    from app.providers.pansou import PanSouProvider

    for cls in (
        BilibiliProvider,
        NeteaseProvider,
        DoubanProvider,
        PanSouProvider,
        InternetArchiveProvider,
        OpenLibraryProvider,
    ):
        PROVIDERS[cls.name] = cls


def get_provider_classes() -> dict[str, type[BaseProvider]]:
    _register_all()
    return dict(PROVIDERS)


def build_providers(
    config: AppConfig, client: AsyncClient
) -> list[BaseProvider]:
    """按配置实例化启用的 Provider（实例化失败的源跳过并记录）."""
    _register_all()
    instances: list[BaseProvider] = []
    for name, pconf in config.providers.items():
        if not pconf.enabled:
            continue
        cls = PROVIDERS.get(name)
        if cls is None:
            logger.warning("未知 provider: %s", name)
            continue
        merged = ProviderConfig(
            enabled=True,
            base_url=pconf.base_url,
            limit=pconf.limit or config.search.per_source_limit,
        )
        try:
            instances.append(cls(client, merged))
        except ProviderError as exc:
            logger.warning("provider %s 初始化失败: %s", name, exc.reason)
    return instances


def provider_infos(config: AppConfig) -> list[ProviderInfo]:
    """全部源的元信息（含未启用），供 /api/providers."""
    _register_all()
    infos: list[ProviderInfo] = []
    for name, cls in PROVIDERS.items():
        pconf = config.providers.get(name)
        enabled = bool(pconf and pconf.enabled)
        infos.append(ProviderInfo(
            name=name,
            enabled=enabled,
            supported_types=list(cls.supported_types),
            status="ok" if enabled else "disabled",
        ))
    return infos


ALL_TYPES: list[ResourceType] = list(ResourceType)
