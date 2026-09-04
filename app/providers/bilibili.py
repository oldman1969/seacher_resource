"""Bilibili Provider：wbi 签名搜索（视频 + 番剧/影视）.

Phase 0 实测：游客态可用，需 Referer + buvid cookie.
付费判定：番剧/影视搜索结果 pay.badge；普通视频 enrich 时查 view 详情 rights.pay.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.config import ProviderConfig, get_secret
from app.models import PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError
from app.utils.wbi import shared_wbi_keys

SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
VIEW_URL = "https://api.bilibili.com/x/web-interface/wbi/view"

# 搜索结果标题带 <em class="keyword"> 高亮
_EM_RE = re.compile(r"<[^>]+>")

SEARCH_TYPES = [
    ("video", ResourceType.video),
    ("media_bangumi", ResourceType.video),
    ("media_ft", ResourceType.video),
]


def _clean_title(title: str) -> str:
    return _EM_RE.sub("", title).strip()


class BilibiliProvider(BaseProvider):
    name = "bilibili"
    supported_types = (ResourceType.video,)
    enabled_by_default = True

    def __init__(self, client, config: ProviderConfig):
        super().__init__(client, config)
        # Referer 是游客态搜索返回非空结果的关键（Phase 0 实测）
        self.headers = {"Referer": "https://www.bilibili.com/"}

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            resources: list[Resource] = []
            for search_type, rtype in SEARCH_TYPES:
                batch = await self._search_type(keyword, search_type, rtype, limit)
                resources.extend(batch)
                if len(resources) >= limit * 2:
                    break
            return resources
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    async def _search_type(
        self, keyword: str, search_type: str, rtype: ResourceType, limit: int
    ) -> list[Resource]:
        params = await shared_wbi_keys.sign(
            self.client,
            {"search_type": search_type, "keyword": keyword, "page": 1},
        )
        resp = await self.client.get(
            SEARCH_URL, params=params, headers=self.headers
        )
        data = resp.json()
        if data.get("code") != 0:
            raise ProviderError(self.name, f"search({search_type}) code={data.get('code')}: {data.get('message')}")
        results = data.get("data", {}).get("result") or []

        out: list[Resource] = []
        for item in results[:limit]:
            bvid = item.get("bvid") or ""
            if not bvid:
                continue
            payment, note = self._payment_from_search(item)
            out.append(Resource(
                resource_id=f"bilibili:{bvid}",
                title=_clean_title(item.get("title", "")),
                type=rtype,
                source=self.name,
                url=f"https://www.bilibili.com/video/{bvid}",
                payment=payment,
                payment_note=note,
                author=item.get("author"),
                cover=(item.get("pic") or "").replace("//", "https://", 1) if item.get("pic") else None,
                publish_date=_parse_pubdate(item.get("pubdate")),
                extra={
                    "play_count": item.get("play"),
                    "danmaku_count": item.get("video_review"),
                    "duration": item.get("duration"),
                    "is_bangumi": search_type != "video",
                    "desc": (item.get("description") or "")[:120],
                },
            ))
        return out

    @staticmethod
    def _payment_from_search(item: dict) -> tuple[PaymentStatus, str | None]:
        """番剧/影视搜索结果的 pay badge → 付费状态."""
        pay = item.get("pay") or {}
        badge = (pay.get("badge") or "").strip()
        if badge:
            if "会员" in badge:
                return PaymentStatus.vip, badge
            if "付费" in badge or "购买" in badge:
                return PaymentStatus.paid, badge
        return PaymentStatus.unknown, None

    async def enrich(self, resource: Resource) -> Resource:
        """普通视频查 view 详情，判定充电专属/付费."""
        if resource.extra.get("is_bangumi"):
            return resource  # 番剧付费已在搜索结果标注
        bvid = resource.resource_id.split(":", 1)[1]
        try:
            params = await shared_wbi_keys.sign(self.client, {"bvid": bvid})
            resp = await self.client.get(VIEW_URL, params=params, headers=self.headers)
            data = resp.json()
            if data.get("code") != 0:
                return resource  # 查不到不改变原状态
            v = data.get("data") or {}
            rights = v.get("rights") or {}
            note = None
            payment = resource.payment
            if v.get("is_upower_exclusive"):
                payment, note = PaymentStatus.paid, "充电专属"
            elif rights.get("pay") == 1 or rights.get("ugc_pay") == 1:
                payment, note = PaymentStatus.paid, "付费视频"
            resource.payment = payment
            resource.payment_note = note or resource.payment_note
            resource.availability = _availability_from_view(data)
        except Exception:  # noqa: BLE001
            pass  # enrich 失败静默保留原状态
        return resource


def _availability_from_view(data: dict):
    from app.models import AvailabilityStatus

    return (
        AvailabilityStatus.available if data.get("code") == 0
        else AvailabilityStatus.unverified
    )


def _parse_pubdate(ts) -> datetime.date | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts)).date()
    except (ValueError, OSError, TypeError):
        return None
