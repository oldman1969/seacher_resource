"""LibriVox Provider：搜 LibriVox 公版有声书（数据托管在 archive.org 的 librivoxaudio 集合）.

LibriVox 官方 API 的 title 是精确匹配、且不稳定；改用 archive.org 的
advancedsearch 按 collection:librivoxaudio 检索，稳定可靠。
直连不可达，需配置代理后由前端「国际源」勾选启用（on-demand）.
"""

from __future__ import annotations

from app.config import ProviderConfig
from app.models import AvailabilityStatus, PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

SEARCH_URL = "https://archive.org/advancedsearch.php"


class LibriVoxProvider(BaseProvider):
    name = "librivox"
    supported_types = (ResourceType.audio,)
    enabled_by_default = False
    requires_proxy = True
    group = "intl"

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            resp = await self.client.get(
                SEARCH_URL,
                params={
                    "q": f"({keyword}) AND collection:librivoxaudio",
                    "fl[]": ["identifier", "title", "creator", "year", "downloads"],
                    "rows": min(limit, 20),
                    "page": 1,
                    "output": "json",
                },
            )
            if resp.status_code != 200:
                raise ProviderError(self.name, f"HTTP {resp.status_code}")
            docs = (resp.json().get("response") or {}).get("docs") or []
            out: list[Resource] = []
            for d in docs[:limit]:
                identifier = d.get("identifier")
                if not identifier:
                    continue
                out.append(Resource(
                    resource_id=f"librivox:{identifier}",
                    title=d.get("title") or identifier,
                    type=ResourceType.audio,
                    source=self.name,
                    url=f"https://archive.org/details/{identifier}",
                    payment=PaymentStatus.free,                 # 公版免费
                    availability=AvailabilityStatus.available,  # 可直接在线听
                    author=_first(d.get("creator")),
                    publish_date=_year_to_date(d.get("year")),
                    extra={"downloads": d.get("downloads"), "collection": "librivoxaudio"},
                ))
            return out
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc


def _first(v) -> str | None:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _year_to_date(year):
    if not year:
        return None
    try:
        from datetime import date

        y = int(str(year)[:4])
        if 1 <= y <= 2100:
            return date(y, 1, 1)
    except (ValueError, TypeError):
        pass
    return None
