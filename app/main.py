"""FastAPI 应用入口：lifespan 管理共享 AsyncClient 与 ProbeEngine."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.aggregator import search_all
from app.api.routes import router
from app.auth import AuthMiddleware, auth_router
from app.config import PROJECT_ROOT, load_config
from app.models import AvailabilityStatus, ResourceType
from app.probe import ProbeEngine
from app.providers.registry import build_providers, provider_infos
from app.utils.http import build_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    client = build_client(proxy=config.proxy)
    providers = build_providers(config, client)
    probe_engine = ProbeEngine(config.probe)

    loaded = [p.name for p in providers]
    logger.info("已加载数据源: %s", loaded or "(无)")

    # ---- 挂到 app.state，路由直接访问 ----
    app.state.client = client
    app.state.providers = providers
    app.state.probe_engine = probe_engine
    app.state.provider_infos = lambda: provider_infos(config)
    app.state.config = config
    app.state.config_root = PROJECT_ROOT  # 管理端点写 .env 用

    async def aggregator_search(
        keyword: str,
        types: list[ResourceType] | None,
        enrich: bool = True,
        depth: bool = False,
        include_intl: bool = False,
        include_social: bool = False,
    ):
        # 每次搜索前热加载配置（增删源无需重启）
        cfg = load_config()
        if _config_changed(cfg, app.state.config):
            app.state.providers = build_providers(cfg, client)
            app.state.config = cfg
        providers = [
            p
            for p in app.state.providers
            if p.group == "default"
            or (p.group == "intl" and include_intl)
            or (p.group == "social" and include_social)
        ]
        limit = cfg.search.depth_limit if depth else cfg.search.per_source_limit
        return await search_all(
            providers, keyword, types, cfg, enrich, limit
        )

    async def auto_probe(grouped: dict[str, list]) -> None:
        """搜索后自动探测每类型 top N（磁力跳过）."""
        cfg = app.state.config
        targets = [
            r
            for items in grouped.values()
            for r in items[: cfg.probe.auto_probe_top]
            if r.availability is AvailabilityStatus.unverified
        ]
        if targets:
            try:
                results = await probe_engine.probe_many(client, targets)
                for r in targets:
                    if r.resource_id in results:
                        r.availability = results[r.resource_id][0]
            except Exception:  # noqa: BLE001
                logger.warning("自动探测失败", exc_info=True)

    app.state.aggregator_search = aggregator_search
    app.state.auto_probe = auto_probe

    yield

    await client.aclose()


def _config_changed(new, old) -> bool:
    return new.model_dump() != old.model_dump()


def create_app() -> FastAPI:
    app = FastAPI(title="全网资源聚合搜索", version="0.1.0", lifespan=lifespan)
    app.add_middleware(AuthMiddleware)
    app.include_router(router)
    app.include_router(auth_router)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
