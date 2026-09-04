"""API 路由：聚合搜索 / 单条探测 / 数据源状态."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query, Request

from app.models import ProbeResponse, ResourceType, SearchResponse

router = APIRouter(prefix="/api")


def _get_state(request: Request):
    return request.app.state


@router.get("/search", response_model=SearchResponse)
async def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100, description="搜索关键词"),
    types: str | None = Query(None, description="逗号分隔: video,book,audio,netdisk_share,magnet"),
    enrich: bool = Query(True, description="是否对 top N 补付费元数据"),
    probe_top: bool = Query(True, description="是否自动探测每类型 top N 可用性"),
) -> SearchResponse:
    state = _get_state(request)
    if not q.strip():
        raise HTTPException(422, "关键词为空")

    type_list: list[ResourceType] | None = None
    if types:
        try:
            type_list = [ResourceType(t.strip()) for t in types.split(",") if t.strip()]
        except ValueError as exc:
            raise HTTPException(422, f"非法类型: {exc}") from exc

    start = time.monotonic()
    grouped, errors = await state.aggregator_search(q.strip(), type_list, enrich)
    elapsed = int((time.monotonic() - start) * 1000)

    if probe_top and state.probe_engine:
        await state.auto_probe(grouped)

    return SearchResponse(keyword=q, results=grouped, errors=errors, elapsed_ms=elapsed)


@router.post("/probe", response_model=ProbeResponse)
async def probe(request: Request, body: dict) -> ProbeResponse:
    """单条实时探测可用性（前端「验证」按钮）."""
    state = _get_state(request)
    resource_id = body.get("resource_id")
    url = body.get("url")
    if not resource_id or not url:
        raise HTTPException(422, "需要 resource_id 与 url")
    pan_type = body.get("pan_type")

    from app.models import AvailabilityStatus, Resource, ResourceType

    resource = Resource(
        resource_id=resource_id,
        title="",
        type=ResourceType(body.get("type", "netdisk_share")),
        source=body.get("source", "unknown"),
        url=url,
        pan_type=pan_type,
    )
    status, detail = await state.probe_engine.probe(state.client, resource)
    import datetime as _dt

    return ProbeResponse(
        resource_id=resource_id,
        availability=status,
        detail=detail,
        checked_at=_dt.datetime.now().isoformat(timespec="seconds"),
    )


@router.get("/providers")
async def providers(request: Request):
    state = _get_state(request)
    infos = state.provider_infos()
    # 合并运行时健康度
    return {
        "providers": [i.model_dump() for i in infos],
        "active": [p.name for p in state.providers],
    }
