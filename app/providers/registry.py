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
    from app.providers.gutenberg import GutenbergProvider
    from app.providers.internet_archive import InternetArchiveProvider
    from app.providers.librivox import LibriVoxProvider
    from app.providers.netease import NeteaseProvider
    from app.providers.openlibrary import OpenLibraryProvider
    from app.providers.pansou import PanSouProvider
    from app.providers.wechat import WechatProvider
    from app.providers.zhihu import ZhihuProvider

    for cls in (
        BilibiliProvider,
        NeteaseProvider,
        DoubanProvider,
        PanSouProvider,
        InternetArchiveProvider,
        OpenLibraryProvider,
        GutenbergProvider,
        LibriVoxProvider,
        ZhihuProvider,
        WechatProvider,
    ):
        PROVIDERS[cls.name] = cls


def get_provider_classes() -> dict[str, type[BaseProvider]]:
    _register_all()
    return dict(PROVIDERS)


def build_providers(
    config: AppConfig, client: AsyncClient
) -> list[BaseProvider]:
    """实例化 Provider（实例化失败的源跳过并记录）.

    普通源按 config 的 enabled 开关实例化；on_demand 源（国际源）无视 enabled
    始终实例化，由搜索请求按需注入（前端「国际源」勾选控制）。
    """
    _register_all()
    instances: list[BaseProvider] = []
    for name, pconf in config.providers.items():
        cls = PROVIDERS.get(name)
        if cls is None:
            if pconf.enabled:
                logger.warning("未知 provider: %s", name)
            continue
        if cls.group == "default" and not pconf.enabled:
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
        on_demand = cls.group != "default"
        enabled = bool(pconf and pconf.enabled) or on_demand
        status = "on_demand" if on_demand else ("ok" if enabled else "disabled")
        infos.append(ProviderInfo(
            name=name,
            enabled=enabled,
            supported_types=list(cls.supported_types),
            status=status,
        ))
    return infos


ALL_TYPES: list[ResourceType] = list(ResourceType)
