"""统一数据模型：五类资源 + 付费/可用性状态."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def _sanitize_text(v: Any) -> Any:
    """递归清洗字符串：丢弃未配对 surrogate（B 站等 API 返回的脏数据，会毒化 JSON）."""
    if isinstance(v, str):
        return v.encode("utf-8", "ignore").decode("utf-8", "ignore")
    if isinstance(v, dict):
        return {k: _sanitize_text(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sanitize_text(x) for x in v]
    return v


class ResourceType(str, Enum):
    book = "book"                      # 书籍/文本
    video = "video"                    # 视频
    audio = "audio"                    # 音乐/播客/有声书
    netdisk_share = "netdisk_share"    # 网盘分享链接
    magnet = "magnet"                  # 磁力/ed2k 链接
    article = "article"                # 文章（知乎回答/文章、公众号等）


class PaymentStatus(str, Enum):
    free = "free"
    paid = "paid"          # 需单次付费购买
    vip = "vip"            # 需会员/VIP
    unknown = "unknown"


class AvailabilityStatus(str, Enum):
    available = "available"
    unverified = "unverified"
    unavailable = "unavailable"


class Resource(BaseModel):
    resource_id: str                       # "<source>:<external_id>"
    title: str
    type: ResourceType
    source: str
    url: str                               # 落地页或 magnet: 链接
    payment: PaymentStatus = PaymentStatus.unknown
    payment_note: str | None = None        # "大会员专属" / "¥12.99" 等
    availability: AvailabilityStatus = AvailabilityStatus.unverified
    pan_type: str | None = None            # baidu/quark/aliyun/xunlei/115/uc/tianyi/123/ed2k...
    password: str | None = None            # 网盘提取码
    cover: str | None = None
    author: str | None = None
    publish_date: date | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _sanitize(cls, data: Any) -> Any:
        return _sanitize_text(data)

    def probe_key(self) -> str:
        """探测缓存键：同 URL 不重复探测."""
        return f"{self.source}|{self.url}"


class ProviderInfo(BaseModel):
    name: str
    enabled: bool
    supported_types: list[ResourceType]
    status: str = "ok"                     # ok / degraded / disabled
    last_error: str | None = None


class SearchResponse(BaseModel):
    keyword: str
    results: dict[str, list[Resource]] = Field(default_factory=dict)
    errors: list[dict[str, str]] = Field(default_factory=list)
    elapsed_ms: int = 0


class ProbeResponse(BaseModel):
    resource_id: str
    availability: AvailabilityStatus
    detail: str | None = None
    checked_at: str
