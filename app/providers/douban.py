"""豆瓣 Provider：suggest 接口元数据增强.

Phase 0 实测：movie.douban.com/j/subject_suggest 可用（限流敏感，需低频）.
只做 suggest 级搜索，不做 subject_search 解密（加密方案轮换，明确排除）.
"""

from __future__ import annotations

import asyncio
import time

from app.config import ProviderConfig
from app.models import Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

MOVIE_SUGGEST_URL = "https://movie.douban.com/j/subject_suggest"
BOOK_SUGGEST_URL = "https://www.douban.com/j/search_suggest"

# suggest 返回 category: movie/tv/book/music...
CATEGORY_TYPE = {
    "movie": ResourceType.video,
    "tv": ResourceType.video,
    "book": ResourceType.book,
    "music": ResourceType.audio,
}

# 全局限速：豆瓣对高频访问风控极敏感（≥2s 间隔）
_last_call = 0.0
_MIN_INTERVAL = 2.0


async def _throttle() -> None:
    global _last_call
    now = time.monotonic()
    wait = _last_call + _MIN_INTERVAL - now
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call = time.monotonic()


class DoubanProvider(BaseProvider):
    name = "douban"
    supported_types = (ResourceType.video, ResourceType.book, ResourceType.audio)
    enabled_by_default = True

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            await _throttle()
            resp = await self.client.get(
                MOVIE_SUGGEST_URL,
                params={"q": keyword},
                headers={"Referer": "https://movie.douban.com/"},
            )
            if resp.status_code != 200:
                raise ProviderError(self.name, f"HTTP {resp.status_code}（可能被限流）")
            items = resp.json()
            out: list[Resource] = []
            for it in items[:limit]:
                rtype = CATEGORY_TYPE.get(it.get("type"))
                if not rtype:
                    continue
                sub = it.get("sub_name") or ""
                title = it.get("title", "")
                if sub:
                    title = f"{title} ({sub})"
                out.append(Resource(
                    resource_id=f"douban:{it.get('id')}",
                    title=title,
                    type=rtype,
                    source=self.name,
                    url=it.get("url", ""),
                    # 豆瓣是目录/评分站：无资源本体，付费状态恒 unknown
                    availability=_availability(it),
                    author=None,
                    cover=(it.get("img") or "").replace("/s_ratio_poster/", "/l_ratio_poster/"),
                    extra={
                        "year": it.get("year"),
                        "category": it.get("type"),
                        "rating": None,  # suggest 接口无评分，评分需详情页
                    },
                ))
            return out
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc


def _availability(it: dict):
    from app.models import AvailabilityStatus

    # suggest 返回的 url 一定是存在的条目页，视为 available
    return AvailabilityStatus.available if it.get("url") else AvailabilityStatus.unverified
