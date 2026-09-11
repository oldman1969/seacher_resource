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
    # 分组：default=常驻源（按 enabled 开关）；intl=国际源（include_intl 控制）；
    # social=知乎/微信（include_social 控制）。非 default 为按需源，前端勾选后注入。
    group: str = "default"
    # 单源超时（秒）；None = 用聚合器全局 deadline。翻页类源（如微信搜狗）需放宽。
    deadline: float | None = None

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
