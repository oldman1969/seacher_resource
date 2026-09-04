"""单元测试：Provider 字段映射与付费判定（respx mock，不依赖真网）."""

import httpx
import pytest
import respx

from app.config import ProviderConfig
from app.models import PaymentStatus, ResourceType
from app.providers.bilibili import BilibiliProvider
from app.providers.netease import NeteaseProvider
from app.providers.pansou import PanSouProvider, _guess_pan_type
from app.utils.wbi import shared_wbi_keys


def _make(provider_cls, base_url=None):
    cfg = ProviderConfig(enabled=True, base_url=base_url, limit=10)
    client = httpx.AsyncClient()
    return provider_cls(client, cfg)


# ---------------------------------------------------------------- bilibili

BILI_SEARCH_RESP = {
    "code": 0,
    "data": {
        "result": [
            {
                "bvid": "BV1xx411c7mD",
                "title": "三体<em class=\"keyword\">动画</em>合集",
                "author": "UP主甲",
                "pic": "//i0.hdslb.com/bfs/archive/abc.jpg",
                "pubdate": 1700000000,
                "play": 123456,
                "duration": "10:30",
                "pay": {},
            },
            {
                "bvid": "BV1yy411c7mE",
                "title": "三体 剧集",
                "author": "UP主乙",
                "pic": "",
                "pubdate": 1700000001,
                "play": 99,
                "duration": "45:00",
                "pay": {"badge": "会员专享"},
            },
        ]
    },
}


@respx.mock
async def test_bilibili_search_and_payment():
    provider = _make(BilibiliProvider)
    # mock: 主页（拿 buvid）、nav（拿 wbi key）、搜索
    respx.get("https://www.bilibili.com/").mock(return_value=httpx.Response(200))
    respx.get("https://api.bilibili.com/x/web-interface/nav").mock(
        return_value=httpx.Response(200, json={
            "code": -101,  # 未登录也带 wbi_img
            "data": {"wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
            }},
        })
    )
    respx.get(url__startswith="https://api.bilibili.com/x/web-interface/wbi/search/type").mock(
        return_value=httpx.Response(200, json=BILI_SEARCH_RESP)
    )

    resources = await provider.search("三体", limit=5)
    # 3 种 search_type（video/media_bangumi/media_ft）× 2 条 = 6
    assert len(resources) == 6
    # 第一条：无 pay badge → unknown（video 类型）
    r1 = next(r for r in resources if "动画" in r.title)
    assert r1.payment is PaymentStatus.unknown
    assert "<em" not in r1.title  # 高亮标签已清洗
    assert r1.url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert r1.author == "UP主甲"
    assert r1.cover.startswith("https://")
    # 第二条：会员 badge → vip
    r2 = next(r for r in resources if "剧集" in r.title)
    assert r2.payment is PaymentStatus.vip
    assert r2.payment_note == "会员专享"


# ---------------------------------------------------------------- netease

NETEASE_RESP = {
    "code": 200,
    "result": {
        "songs": [
            {"id": 111, "name": "晴天", "fee": 8, "artists": [{"name": "周杰伦"}], "album": {"name": "叶惠美"}, "duration": 269000},
            {"id": 222, "name": "VIP歌", "fee": 1, "artists": [{"name": "歌手B"}], "album": {"name": "专辑B"}, "duration": 200000},
            {"id": 333, "name": "付费专辑歌", "fee": 4, "artists": [{"name": "歌手C"}], "album": {}, "duration": 180000},
        ]
    },
}


@respx.mock
async def test_netease_fee_mapping():
    provider = _make(NeteaseProvider)
    respx.post("https://music.163.com/api/search/get/web").mock(
        return_value=httpx.Response(200, json=NETEASE_RESP)
    )
    resources = await provider.search("晴天", limit=5)
    assert len(resources) == 3
    by_name = {r.title: r for r in resources}
    assert by_name["晴天"].payment is PaymentStatus.free
    assert "低音质" in by_name["晴天"].payment_note
    assert by_name["VIP歌"].payment is PaymentStatus.vip
    assert by_name["付费专辑歌"].payment is PaymentStatus.paid
    assert by_name["晴天"].type is ResourceType.audio


@respx.mock
async def test_netease_error_raises_provider_error():
    from app.providers.base import ProviderError

    provider = _make(NeteaseProvider)
    respx.post("https://music.163.com/api/search/get/web").mock(
        return_value=httpx.Response(200, json={"code": 400, "result": {}})
    )
    with pytest.raises(ProviderError):
        await provider.search("x")


# ---------------------------------------------------------------- pansou

# 实测 PanSou 响应结构（2026-09）：data.merged_by_type = {pan_type: [item]}
# item: {url, password, note, datetime, source}
PANSOU_RESP = {
    "code": 0,
    "data": {
        "total": 2,
        "merged_by_type": {
            "baidu": [
                {
                    "url": "https://pan.baidu.com/s/1abcdEFGhijk?pwd=8x2k",
                    "password": "8x2k",
                    "note": "三体 有声书 全本",
                    "datetime": "2026-08-05T01:53:02Z",
                    "source": "plugin:melost",
                }
            ],
            "magnet": [
                {
                    "url": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=santi",
                    "password": "",
                    "note": "三体.全集.mkv",
                    "datetime": "2026-07-01T10:00:00Z",
                    "source": "plugin:rrbt",
                }
            ],
        },
    },
}


@respx.mock
async def test_pansou_mapping():
    provider = _make(PanSouProvider, base_url="http://127.0.0.1:18888")
    respx.post("http://127.0.0.1:18888/api/search").mock(
        return_value=httpx.Response(200, json=PANSOU_RESP)
    )
    resources = await provider.search("三体", limit=5)
    assert len(resources) == 2

    baidu = next(r for r in resources if r.pan_type == "baidu")
    assert baidu.type is ResourceType.netdisk_share
    assert baidu.password == "8x2k"
    assert baidu.payment is PaymentStatus.free
    assert baidu.resource_id == "pansou:1abcdEFGhijk"
    assert baidu.title == "三体 有声书 全本"
    assert baidu.author == "melost"  # plugin: 前缀已去除
    assert baidu.publish_date is not None

    magnet = next(r for r in resources if r.pan_type == "magnet")
    assert magnet.type is ResourceType.magnet
    assert magnet.url.startswith("magnet:")


@respx.mock
async def test_pansou_legacy_flat_format():
    """旧版平铺 data 列表格式的兼容."""
    provider = _make(PanSouProvider, base_url="http://127.0.0.1:18888")
    respx.post("http://127.0.0.1:18888/api/search").mock(
        return_value=httpx.Response(200, json={
            "data": [
                {"name": "旧版结果", "url": "https://pan.baidu.com/s/1xyz?pwd=abcd",
                 "type": "baidu", "password": "abcd"},
            ]
        })
    )
    resources = await provider.search("x", limit=5)
    assert len(resources) == 1
    assert resources[0].pan_type == "baidu"
    assert resources[0].password == "abcd"


def test_guess_pan_type():
    assert _guess_pan_type("https://pan.baidu.com/s/1abc") == "baidu"
    assert _guess_pan_type("https://www.alipan.com/s/xyz") == "aliyun"
    assert _guess_pan_type("magnet:?xt=urn:btih:aaa") == "magnet"
    assert _guess_pan_type("ed2k://|file|x|1|abc|/") == "ed2k"
    assert _guess_pan_type("https://unknown.com/s/1") == "others"


@respx.mock
async def test_pansou_unreachable_raises():
    from app.providers.base import ProviderError

    provider = _make(PanSouProvider, base_url="http://127.0.0.1:19999")
    respx.post("http://127.0.0.1:19999/api/search").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with pytest.raises(ProviderError):
        await provider.search("x")


@respx.mock
async def test_pansou_extension_fallback():
    """带扩展名的冷词 0 结果时，自动去扩展名重搜."""
    provider = _make(PanSouProvider, base_url="http://127.0.0.1:18888")
    route = respx.post("http://127.0.0.1:18888/api/search")

    # 第一次（斩神.txt）返回空，第二次（斩神）返回结果
    responses = [
        httpx.Response(200, json={"code": 0, "data": {"total": 0, "merged_by_type": {}}}),
        httpx.Response(200, json={
            "code": 0,
            "data": {"total": 1, "merged_by_type": {
                "baidu": [{"url": "https://pan.baidu.com/s/1zz99", "password": "ab12",
                           "note": "斩神 全本", "datetime": "2026-08-01T00:00:00Z",
                           "source": "plugin:xiaokupan"}]
            }},
        }),
    ]
    route.side_effect = responses

    resources = await provider.search("斩神.txt", 5)
    assert len(resources) == 1
    assert resources[0].title == "斩神 全本"
    # 两次调用的关键词分别是原词和去扩展名词
    import json as _json
    assert _json.loads(route.calls[0].request.read())["kw"] == "斩神.txt"
    assert _json.loads(route.calls[1].request.read())["kw"] == "斩神"


@respx.mock
async def test_pansou_no_fallback_when_results():
    """有结果时不做降级（只发一次请求）."""
    provider = _make(PanSouProvider, base_url="http://127.0.0.1:18888")
    route = respx.post("http://127.0.0.1:18888/api/search")
    route.mock(return_value=httpx.Response(200, json=PANSOU_RESP))

    resources = await provider.search("三体.txt", 5)
    assert len(resources) == 2
    assert len(route.calls) == 1
