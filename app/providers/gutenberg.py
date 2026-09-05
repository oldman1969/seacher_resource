"""Gutenberg Provider：直接搜 gutenberg.org 官方电子书目录（公版，无鉴权）.

Gutendex 等第三方聚合 API 偶发超时/不稳定，官方站更可靠。
直连不可达，需配置代理后由前端「国际源」勾选启用（on-demand）.
"""

from __future__ import annotations

import html
import re

from app.config import ProviderConfig
from app.models import AvailabilityStatus, PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

SEARCH_URL = "https://www.gutenberg.org/ebooks/search/"

_LI_RE = re.compile(r'<li class="booklink">(.*?)</li>', re.S)
_ID_RE = re.compile(r'href="/ebooks/(\d+)"')
_TITLE_RE = re.compile(r'<span class="title">(.*?)</span>', re.S)
_SUBTITLE_RE = re.compile(r'<span class="subtitle">(.*?)</span>', re.S)
_EXTRA_RE = re.compile(r'<span class="extra">(.*?)</span>', re.S)
_COVER_RE = re.compile(r'<img[^>]+src="([^"]+)"')
_DL_RE = re.compile(r"(\d+)")


class GutenbergProvider(BaseProvider):
    name = "gutenberg"
    supported_types = (ResourceType.book,)
    enabled_by_default = False
    requires_proxy = True
    on_demand = True

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            resp = await self.client.get(
                SEARCH_URL, params={"query": keyword}, timeout=15.0
            )
            if resp.status_code != 200:
                raise ProviderError(self.name, f"HTTP {resp.status_code}")
            return self._parse(resp.text, limit)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    def _parse(self, text: str, limit: int) -> list[Resource]:
        out: list[Resource] = []
        for block in _LI_RE.findall(text):
            if len(out) >= limit:
                break
            m = _ID_RE.search(block)
            if not m:
                continue
            bid = m.group(1)
            title = _clean(_TITLE_RE.search(block))
            if not title:
                continue
            author = _clean(_SUBTITLE_RE.search(block)) or None
            extra_text = _clean(_EXTRA_RE.search(block))
            cover = _COVER_RE.search(block)
            cover_url = None
            if cover:
                cover_url = "https://www.gutenberg.org" + cover.group(1).replace(
                    ".small.", ".medium."
                )
            downloads = None
            if extra_text:
                dm = _DL_RE.search(extra_text.replace(",", ""))
                if dm:
                    downloads = int(dm.group(1))
            out.append(Resource(
                resource_id=f"gutenberg:{bid}",
                title=title,
                type=ResourceType.book,
                source=self.name,
                url=f"https://www.gutenberg.org/ebooks/{bid}",
                payment=PaymentStatus.free,                 # 公版免费
                availability=AvailabilityStatus.available,  # 直接下载，恒可用
                author=author,
                cover=cover_url,
                extra={"download_count": downloads, "downloads_text": extra_text},
            ))
        return out


def _clean(match) -> str:
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()
