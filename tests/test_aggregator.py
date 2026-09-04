"""单元测试：聚合器（并发调度/错误隔离/分组排序）."""

import httpx
import pytest

from app.config import AppConfig, ProviderConfig, SearchConfig
from app.models import PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError


class FakeProvider(BaseProvider):
    name = "fake"
    supported_types = (ResourceType.video,)

    def __init__(self, resources=None, error=None, delay=0.0):
        super().__init__(httpx.AsyncClient(), ProviderConfig(enabled=True))
        self.resources = resources or []
        self.error = error
        self.delay = delay

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        if self.error:
            raise ProviderError(self.name, self.error)
        return self.resources[:limit]


def _res(source: str, title: str, rtype=ResourceType.video, payment=PaymentStatus.free, play=None) -> Resource:
    return Resource(
        resource_id=f"{source}:{title}",
        title=title,
        type=rtype,
        source=source,
        url=f"https://example.com/{title}",
        payment=payment,
        extra={"play_count": play},
    )


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(search=SearchConfig(deadline=1.0, per_source_limit=10, enrich_top=5))


async def test_results_grouped_and_ranked(config):
    p1 = FakeProvider([_res("a", "三体 完整版", play=1000000), _res("a", "随便")])
    p2 = FakeProvider(
        [_res("b", "三体 解说", rtype=ResourceType.audio), _res("b", "三体 VIP 版", payment=PaymentStatus.vip)]
    )
    from app.aggregator import search_all

    grouped, errors = await search_all([p1, p2], "三体", None, config, enrich=False)
    assert errors == []
    assert set(grouped) == {"video", "audio"}
    # 免费且相关度高的排前面
    assert grouped["video"][0].title == "三体 完整版"
    assert grouped["audio"][0].payment is PaymentStatus.free


async def test_single_source_failure_isolated(config):
    ok = FakeProvider([_res("ok", "结果")])
    bad = FakeProvider(error="接口挂了")
    from app.aggregator import search_all

    grouped, errors = await search_all([ok, bad], "x", None, config, enrich=False)
    assert len(grouped.get("video", [])) == 1  # 好源结果完好
    assert errors == [{"source": "fake", "error": "接口挂了"}]


async def test_timeout_isolated(config):
    import asyncio

    slow = FakeProvider(delay=2.0)  # 超过 deadline=1s
    fast = FakeProvider([_res("fast", "快")])
    from app.aggregator import search_all

    grouped, errors = await search_all([slow, fast], "x", None, config, enrich=False)
    assert len(grouped.get("video", [])) == 1
    assert any("超时" in e["error"] for e in errors)


async def test_type_filter(config):
    p = FakeProvider([
        _res("a", "视频", rtype=ResourceType.video),
        _res("a", "音频", rtype=ResourceType.audio),
    ])
    from app.aggregator import search_all

    grouped, _ = await search_all([p], "x", [ResourceType.video], config, enrich=False)
    assert "video" in grouped and "audio" not in grouped
