"""Internet Archive Provider：官方 advancedsearch API（无鉴权）.

Phase 0 实测：当前网络直连超时，需配置代理后启用.
"""

from __future__ import annotations

from app.config import ProviderConfig
from app.models import PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

SEARCH_URL = "https://archive.org/advancedsearch.php"

# mediatype → ResourceType
MEDIA_TYPE_MAP = {
    "audio": ResourceType.audio,
    "movies": ResourceType.video,
    "texts": ResourceType.book,
}


class InternetArchiveProvider(BaseProvider):
    name = "internet_archive"
    supported_types = (ResourceType.video, ResourceType.audio, ResourceType.book)
    enabled_by_default = False
    requires_proxy = True
    on_demand = True

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            out: list[Resource] = []
            for mediatype, rtype in MEDIA_TYPE_MAP.items():
                resp = await self.client.get(
                    SEARCH_URL,
                    params={
                        "q": f'({keyword}) AND mediatype:{mediatype}',
                        "fl[]": ["identifier", "title", "creator", "year", "downloads"],
                        "rows": min(limit, 8),
                        "page": 1,
                        "output": "json",
                    },
                )
                if resp.status_code != 200:
                    raise ProviderError(self.name, f"HTTP {resp.status_code}")
                docs = (resp.json().get("response") or {}).get("docs") or []
                for d in docs:
                    identifier = d.get("identifier")
                    if not identifier:
                        continue
                    year = d.get("year")
                    out.append(Resource(
                        resource_id=f"ia:{identifier}",
                        title=d.get("title", "") or identifier,
                        type=rtype,
                        source=self.name,
                        url=f"https://archive.org/details/{identifier}",
                        payment=PaymentStatus.free,   # IA 恒免费
                        payment_note=None,
                        author=(d.get("creator") or [None])[0] if isinstance(d.get("creator"), list) else d.get("creator"),
                        publish_date=_year_to_date(year),
                        extra={"downloads": d.get("downloads"), "mediatype": mediatype},
                    ))
                if len(out) >= limit:
                    break
            return out[:limit]
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc


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
