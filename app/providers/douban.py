"""豆瓣 Provider：suggest 接口元数据增强（影视 + 书籍）.

Phase 0 实测：
- movie.douban.com/j/subject_suggest 可用（影视，限流敏感需低频）
- book.douban.com/j/subject_suggest 可用（书籍，返回 title/author_name/pic/year）
只做 suggest 级搜索，不做 subject_search 解密（加密方案轮换，明确排除）.
"""

from __future__ import annotations

import asyncio
import time

from app.config import ProviderConfig
from app.models import AvailabilityStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

MOVIE_SUGGEST_URL = "https://movie.douban.com/j/subject_suggest"
BOOK_SUGGEST_URL = "https://book.douban.com/j/subject_suggest"

# 影视 suggest 的 type 字段 → 资源类型
MOVIE_TYPE = {
    "movie": ResourceType.video,
    "tv": ResourceType.video,
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
        """同时搜影视 + 书籍；任一接口成功即有结果，都失败才抛错."""
        out: list[Resource] = []
        errors: list[str] = []
        try:
            out.extend(await self._search_movie(keyword, limit))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"影视: {exc}")
        try:
            out.extend(await self._search_book(keyword, limit))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"书籍: {exc}")
        if not out and errors:
            raise ProviderError(self.name, "; ".join(errors))
        return out

    async def _search_movie(self, keyword: str, limit: int) -> list[Resource]:
        await _throttle()
        resp = await self.client.get(
            MOVIE_SUGGEST_URL,
            params={"q": keyword},
            headers={"Referer": "https://movie.douban.com/"},
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"影视 HTTP {resp.status_code}（可能被限流）")
        out: list[Resource] = []
        for it in resp.json()[:limit]:
            rtype = MOVIE_TYPE.get(it.get("type"))
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
                availability=AvailabilityStatus.available if it.get("url") else AvailabilityStatus.unverified,
                author=None,
                cover=_upscale(it.get("img")),
                extra={"year": it.get("year"), "category": it.get("type"), "rating": None},
            ))
        return out

    async def _search_book(self, keyword: str, limit: int) -> list[Resource]:
        await _throttle()
        resp = await self.client.get(
            BOOK_SUGGEST_URL,
            params={"q": keyword},
            headers={"Referer": "https://book.douban.com/"},
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"书籍 HTTP {resp.status_code}（可能被限流）")
        out: list[Resource] = []
        for it in resp.json()[:limit]:
            url = it.get("url") or ""
            if not url:
                continue
            out.append(Resource(
                resource_id=f"douban:{it.get('id')}",
                title=it.get("title", ""),
                type=ResourceType.book,
                source=self.name,
                url=url,
                # 豆瓣是书目/评分站：条目页有效，但无资源本体（电子书需另找）
                availability=AvailabilityStatus.available,
                author=it.get("author_name"),
                cover=_upscale(it.get("pic")),
                extra={"year": it.get("year"), "category": "book", "rating": None},
            ))
        return out


def _upscale(pic: str | None) -> str | None:
    """豆瓣图片尺寸 s(小) → l(大)."""
    if not pic:
        return None
    return pic.replace("/subject/s/", "/subject/l/").replace("/s_ratio_poster/", "/l_ratio_poster/")
