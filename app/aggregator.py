"""聚合器：并发调度所有 Provider，超时与错误隔离，enrich 编排."""

from __future__ import annotations

import asyncio
import logging
import time

from app.config import AppConfig
from app.models import AvailabilityStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError
from app.ranking import rank

logger = logging.getLogger(__name__)


async def search_all(
    providers: list[BaseProvider],
    keyword: str,
    types: list[ResourceType] | None,
    config: AppConfig,
    enrich: bool = True,
    per_source_limit: int | None = None,
) -> tuple[dict[str, list[Resource]], list[dict[str, str]]]:
    """并发搜索所有源.

    返回 (按类型分组的结果, 失败源列表)。单源失败/超时不影响其他源.
    per_source_limit: 覆盖 config.search.per_source_limit（深度搜索时用）.
    """
    errors: list[dict[str, str]] = []
    raw: list[Resource] = []
    deadline = config.search.deadline
    limit = per_source_limit or config.search.per_source_limit

    async def run_one(provider: BaseProvider) -> None:
        try:
            resources = await asyncio.wait_for(
                provider.search(keyword, limit),
                timeout=deadline,
            )
            # 按请求的类型过滤
            if types:
                resources = [r for r in resources if r.type in types]
            raw.extend(resources)
        except asyncio.TimeoutError:
            errors.append({"source": provider.name, "error": f"超时(>{deadline}s)"})
        except ProviderError as exc:
            errors.append({"source": exc.source, "error": exc.reason})
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": provider.name, "error": f"{type(exc).__name__}: {exc}"})

    async with asyncio.TaskGroup() as tg:
        for p in providers:
            tg.create_task(run_one(p))

    grouped = _group_and_rank(raw, keyword, types)

    if enrich:
        grouped = await _enrich_top(providers, grouped, raw, config)

    return grouped, errors


def _group_and_rank(
    resources: list[Resource],
    keyword: str,
    types: list[ResourceType] | None,
) -> dict[str, list[Resource]]:
    grouped: dict[str, list[Resource]] = {}
    for r in resources:
        grouped.setdefault(r.type.value, []).append(r)
    for t, items in grouped.items():
        grouped[t] = rank(items, keyword)
    # 保持稳定输出顺序
    order = [t.value for t in (types or list(ResourceType))]
    return {t: grouped[t] for t in order if t in grouped} | {
        k: v for k, v in grouped.items() if k not in order
    }


async def _enrich_top(
    providers: list[BaseProvider],
    grouped: dict[str, list[Resource]],
    raw: list[Resource],
    config: AppConfig,
) -> dict[str, list[Resource]]:
    """对每类型 top N 调 provider.enrich() 补付费元数据（单条 3s 超时）."""
    top_n = config.search.enrich_top
    by_id = {r.resource_id: r for r in raw}
    provider_map = {p.name: p for p in providers}

    tasks: list[asyncio.Task] = []
    for items in grouped.values():
        for r in items[:top_n]:
            p = provider_map.get(r.source)
            if p is None:
                continue
            tasks.append(asyncio.create_task(_safe_enrich(p, r)))

    if tasks:
        await asyncio.gather(*tasks)

    # enrich 可能原地修改了 Resource（同一对象在 grouped/raw 中共享）
    for r in by_id.values():
        if r.availability is AvailabilityStatus.unverified and r.extra.get("_api_alive"):
            r.availability = AvailabilityStatus.available
    return grouped


async def _safe_enrich(provider: BaseProvider, resource: Resource) -> None:
    try:
        await asyncio.wait_for(provider.enrich(resource), timeout=3.0)
    except Exception:  # noqa: BLE001
        logger.debug("enrich %s 失败", resource.resource_id, exc_info=True)
