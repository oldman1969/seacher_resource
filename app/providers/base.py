"""Provider 抽象基类与错误类型."""

from __future__ import annotations

from abc import ABC, abstractmethod

from httpx import AsyncClient

from app.config import ProviderConfig
from app.models import Resource, ResourceType


class ProviderError(Exception):
    """单个数据源失败，不影响其他源."""

    def __init__(self, source: str, reason: str):
        self.source = source
        self.reason = reason
        super().__init__(f"[{source}] {reason}")


class BaseProvider(ABC):
    name: str = "base"
    supported_types: tuple[ResourceType, ...] = ()
    enabled_by_default: bool = False
    requires_proxy: bool = False    # 网络不可直连的国际源标记

    def __init__(self, client: AsyncClient, config: ProviderConfig):
        self.client = client
        self.config = config
        self.limit = config.limit

    @abstractmethod
    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        """搜索，返回统一 Resource 列表；失败抛 ProviderError."""

    async def enrich(self, resource: Resource) -> Resource:
        """可选：补付费/可用性元数据（仅对 top N 调用）. 默认 no-op."""
        return resource

    async def health_check(self) -> bool:
        """探测源是否可用（用于 /api/providers）."""
        return True
