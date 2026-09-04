"""Open Library Provider：官方 search.json API.

Phase 0 实测：当前网络直连超时，需配置代理后启用.
"""

from __future__ import annotations

from app.config import ProviderConfig
from app.models import AvailabilityStatus, PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

SEARCH_URL = "https://openlibrary.org/search.json"
FIELDS = "key,title,author_name,first_publish_year,cover_i,isbn,ebook_access"


class OpenLibraryProvider(BaseProvider):
    name = "openlibrary"
    supported_types = (ResourceType.book,)
    enabled_by_default = False
    requires_proxy = True

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            resp = await self.client.get(
                SEARCH_URL,
                params={"q": keyword, "limit": limit, "fields": FIELDS},
            )
            if resp.status_code != 200:
                raise ProviderError(self.name, f"HTTP {resp.status_code}")
            data = resp.json()
            out: list[Resource] = []
            for d in data.get("docs") or []:
                key = d.get("key", "")
                if not key:
                    continue
                ebook = d.get("ebook_access") or "unknown"
                # public = 可在线阅读(免费)；borrowable = 需登录免费借阅
                if ebook == "public":
                    payment, note = PaymentStatus.free, "公版可在线阅读"
                    avail = AvailabilityStatus.available
                elif ebook in ("borrowable", "printdisabled"):
                    payment, note = PaymentStatus.free, "可借阅（需注册）"
                    avail = AvailabilityStatus.available
                else:
                    payment, note = PaymentStatus.unknown, None
                    avail = AvailabilityStatus.unverified
                authors = d.get("author_name") or []
                cover_i = d.get("cover_i")
                out.append(Resource(
                    resource_id=f"ol:{key.strip('/').split('/')[-1]}",
                    title=d.get("title", ""),
                    type=ResourceType.book,
                    source=self.name,
                    url=f"https://openlibrary.org{key}",
                    payment=payment,
                    payment_note=note,
                    availability=avail,
                    author=authors[0] if authors else None,
                    cover=f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else None,
                    publish_date=_year(d.get("first_publish_year")),
                    extra={"isbn": (d.get("isbn") or [None])[0]},
                ))
            return out
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc


def _year(y):
    if not y:
        return None
    try:
        from datetime import date

        return date(int(y), 1, 1)
    except (ValueError, TypeError):
        return None
