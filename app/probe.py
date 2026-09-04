"""HTTP 可用性探测器：网盘深度校验优先，通用 HTTP 兜底.

- per-domain 信号量 + 全局信号量限流
- TTL 内存缓存（同 URL 1h 内不重复探测）
- 状态映射：2xx/3xx→available；404/410→unavailable；
  403/412/429→unverified（反爬拦截≠资源失效）；超时/DNS→unavailable
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import httpx

from app.config import ProbeConfig
from app.models import AvailabilityStatus, Resource
from app.netdisk.validators import detect_pan_type, validate_share

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ProbeEngine:
    def __init__(self, config: ProbeConfig):
        self.config = config
        self._domain_sems: dict[str, asyncio.Semaphore] = {}
        self._global_sem = asyncio.Semaphore(config.global_concurrency)
        self._cache: dict[str, tuple[AvailabilityStatus, str, float]] = {}

    def _domain_sem(self, domain: str) -> asyncio.Semaphore:
        if domain not in self._domain_sems:
            self._domain_sems[domain] = asyncio.Semaphore(
                self.config.per_domain_concurrency
            )
        return self._domain_sems[domain]

    def cached(self, key: str) -> tuple[AvailabilityStatus, str] | None:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[2] < self.config.cache_ttl:
            return hit[0], hit[1]
        return None

    async def probe(self, client: httpx.AsyncClient, resource: Resource) -> tuple[AvailabilityStatus, str]:
        """探测单个资源：磁力恒 unverified；网盘走深度校验；其余通用 HTTP."""
        if resource.type.value == "magnet":
            return AvailabilityStatus.unverified, "磁力链接不做 DHT 探测"

        key = resource.probe_key()
        cached = self.cached(key)
        if cached:
            return cached

        url = resource.url
        pan_type = resource.pan_type or detect_pan_type(url)
        if pan_type:
            status, detail = await validate_share(client, url, pan_type)
        else:
            status, detail = await self._http_probe(client, url)

        self._cache[key] = (status, detail, time.monotonic())
        return status, detail

    async def probe_many(
        self, client: httpx.AsyncClient, resources: list[Resource]
    ) -> dict[str, tuple[AvailabilityStatus, str]]:
        """批量探测（并发受信号量约束），返回 resource_id → (状态, 说明)."""
        results: dict[str, tuple[AvailabilityStatus, str]] = {}

        async def one(r: Resource) -> None:
            try:
                results[r.resource_id] = await self.probe(client, r)
            except Exception as exc:  # noqa: BLE001
                results[r.resource_id] = (
                    AvailabilityStatus.unverified,
                    f"{type(exc).__name__}: {exc}",
                )

        async with asyncio.TaskGroup() as tg:
            for r in resources:
                tg.create_task(one(r))
        return results

    async def _http_probe(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[AvailabilityStatus, str]:
        domain = urlparse(url).netloc
        timeout = httpx.Timeout(self.config.timeout_connect, read=self.config.timeout_read)
        headers = {"User-Agent": UA, "Accept-Encoding": "identity"}

        async with self._global_sem, self._domain_sem(domain):
            try:
                resp = await client.head(url, headers=headers, timeout=timeout,
                                         follow_redirects=True)
                if resp.status_code in (403, 405, 404):
                    # 部分 HEAD 不被支持或被拦，退化 GET 只取首字节
                    resp = await client.get(
                        url,
                        headers={**headers, "Range": "bytes=0-0"},
                        timeout=timeout,
                        follow_redirects=True,
                    )
                return self._map_status(resp.status_code)
            except httpx.TimeoutException:
                return AvailabilityStatus.unavailable, "超时"
            except httpx.HTTPError as exc:
                return AvailabilityStatus.unavailable, f"{type(exc).__name__}"

    @staticmethod
    def _map_status(code: int) -> tuple[AvailabilityStatus, str]:
        if 200 <= code < 400:
            return AvailabilityStatus.available, f"HTTP {code}"
        if code in (404, 410):
            return AvailabilityStatus.unavailable, f"HTTP {code}"
        if code in (403, 412, 429):
            # 反爬拦截不代表资源失效
            return AvailabilityStatus.unverified, f"HTTP {code}（被拦截，不代表失效）"
        return AvailabilityStatus.unverified, f"HTTP {code}"
