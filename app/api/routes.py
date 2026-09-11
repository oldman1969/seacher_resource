"""API 路由：聚合搜索 / 单条探测 / 数据源状态."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query, Request, Response

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
    depth: bool = Query(False, description="深度搜索：每源上限提升到 depth_limit（默认 2000）"),
    include_intl: bool = Query(False, description="是否包含国际源（需配置代理）"),
    include_social: bool = Query(False, description="是否包含知乎/微信（需 cookie / sidecar）"),
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
    grouped, errors = await state.aggregator_search(
        q.strip(), type_list, enrich, depth, include_intl, include_social
    )
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


# ------------------------------------------------------------ 管理端点
# 独立管理密码 ADMIN_PASSWORD（.env 配置，与站点访问密码 ACCESS_PASSWORD 分离）：
# 验证通过后种 2 小时 admin cookie，仅对 /api/admin/* 生效（不能访问站点其他内容）。
# ADMIN_PASSWORD 未设置时设置面板锁定。cookie 只写服务器端，绝不下发给前端。

_ADMIN_TTL = 2 * 60 * 60  # admin 会话 2 小时


def _require_admin(request: Request) -> None:
    """校验 admin 会话。三态语义（前端据此区分界面）：
    - ADMIN_PASSWORD 未设置 → 403（管理功能锁定，非「需要密码」）
    - 已设置但 admin cookie 无效 → 401（需要验证密码）
    - 验证通过 → 正常返回
    """
    from app.auth import ADMIN_COOKIE_NAME, _admin_password, _verify_token

    pw = _admin_password()
    if pw is None:
        raise HTTPException(403, "未配置管理密码（在服务器 .env 设 ADMIN_PASSWORD 后可用）")
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not (bool(token) and _verify_token(token, pw)):
        raise HTTPException(401, "请先验证管理密码")


@router.post("/admin/auth")
async def admin_auth(body: dict, response: Response):
    """设置面板的管理密码验证：通过后种 2 小时 admin cookie."""
    import hmac as _hmac

    from app.auth import ADMIN_COOKIE_NAME, _admin_password, _make_token

    pw = _admin_password()
    if pw is None:
        response.status_code = 403
        return {"detail": "未配置管理密码（在服务器 .env 设 ADMIN_PASSWORD 后可用）"}
    provided = str(body.get("password") or "")
    if not _hmac.compare_digest(provided, pw):
        response.status_code = 401
        return {"detail": "密码错误"}
    response.set_cookie(
        ADMIN_COOKIE_NAME, _make_token(pw, _ADMIN_TTL),
        max_age=_ADMIN_TTL, httponly=True, samesite="lax",
    )
    return {"ok": True}


@router.get("/admin/zhihu-cookie/status")
async def zhihu_cookie_status(request: Request):
    _require_admin(request)
    import os

    return {"configured": bool(os.environ.get("ZHIHU_COOKIE", "").strip())}


@router.post("/admin/zhihu-cookie")
async def update_zhihu_cookie(request: Request, body: dict):
    """更新知乎 cookie（admin 验证通过后调用；写入 .env 持久化 + 当前进程立即生效）."""
    import os

    _require_admin(request)
    cookie = str(body.get("cookie") or "").strip()
    if not cookie:
        raise HTTPException(422, "cookie 不能为空")

    # 直接读写 .env（不用 dotenv.set_key：它内部 os.replace 重命名临时文件，
    # 对 Docker 单文件 bind mount `./.env:/app/.env` 会报 device busy 导致 500）。
    # 保留其他键，只更新 ZHIHU_COOKIE；写入后同步当前进程环境变量（provider 热读）。
    import app.config as config_mod

    env_path = config_mod.PROJECT_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated = False
    for i, ln in enumerate(lines):
        if ln.startswith("ZHIHU_COOKIE="):
            lines[i] = f"ZHIHU_COOKIE={cookie}"
            updated = True
            break
    if not updated:
        lines.append(f"ZHIHU_COOKIE={cookie}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    os.environ["ZHIHU_COOKIE"] = cookie
    return {"ok": True}
