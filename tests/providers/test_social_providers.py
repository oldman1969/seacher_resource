"""单元测试：知乎 + 微信 Provider（respx mock，不依赖真网/cookie）."""

import httpx
import pytest
import respx

from app.config import ProviderConfig
from app.models import PaymentStatus, ResourceType
from app.providers.wechat import WechatProvider
from app.providers.zhihu import ZhihuProvider


def _make(provider_cls, base_url=None):
    cfg = ProviderConfig(enabled=True, base_url=base_url, limit=10)
    return provider_cls(httpx.AsyncClient(), cfg)


@pytest.fixture(autouse=True)
def _reset_wechat_globals():
    """每个用例前重置 wechat 的全局节流/冷却状态；间隔设 0 加速测试."""
    from app.providers import wechat as wechat_mod

    wechat_mod._last_call = 0.0
    wechat_mod._cooldown_until = 0.0
    wechat_mod._MIN_INTERVAL = 0.0
    wechat_mod._MAX_INTERVAL = 0.0
    yield
    wechat_mod._last_call = 0.0
    wechat_mod._cooldown_until = 0.0


ZHIHU_RESP = {
    "data": [
        {
            "type": "search_result",
            "object": {
                "type": "answer",
                "id": 123456,
                "title": "如何评价《三体》",
                "url": "https://www.zhihu.com/question/1/answer/123456",
                "excerpt": "三体是一部伟大的科幻小说",
                "author": {"name": "张三"},
                "voteup_count": 5000,
                "created_time": 1600000000,
            },
        },
        {
            "type": "search_result",
            "object": {
                "type": "article",
                "id": 789,
                "title": "三体深度解读",
                "url": "https://zhuanlan.zhihu.com/p/789",
                "excerpt": "解读三体",
                "author": {"name": "李四"},
                "voteup_count": 100,
                "paid_info": True,
            },
        },
    ]
}

# 搜狗微信搜索结果页 HTML 片段（实测结构，2026-09）
SOGOU_HTML = """
<div class="txt-box">
<h3>
<a target="_blank" href="/link?url=dn9a_-gY295K0Rci&amp;type=2&amp;query=%E4%B8%89%E4%BD%93&amp;token=034F6EBB" id="sogou_vr_11002601_title_0" uigs="article_title_0">国产「<em><!--red_beg-->三体<!--red_end--></em>」为什么必须这么改</a>
</h3>
<p class="txt-info" id="sogou_vr_11002601_summary_0">《<em><!--red_beg-->三体<!--red_end--></em>》的创作原点,大概生发于刘慈欣的一个忧虑</p>
<div class="s-p">
<span class="all-time-y2">Sir电影</span><span class="s2"><script>document.write(timeConvert('1674035136'))</script></span>
</div>
</div>

<div class="txt-box">
<h3>
<a target="_blank" href="/link?url=abc123def&amp;type=2" uigs="article_title_1">《三体》动画版,我是你的催更人</a>
</h3>
<p class="txt-info">三体动画相关内容</p>
<div class="s-p">
<span class="all-time-y2">新周刊</span><span class="s2"><script>document.write(timeConvert('1561632554'))</script></span>
</div>
</div>
"""


# ---------------------------------------------------------------- zhihu


@respx.mock
async def test_zhihu_search_parses_articles(monkeypatch):
    monkeypatch.setenv("ZHIHU_COOKIE", "d_c0=test_dc0; z_c0=test_zc0")
    provider = _make(ZhihuProvider)

    respx.get(url__startswith="https://www.zhihu.com/api/v4/search_v3").mock(
        return_value=httpx.Response(200, json=ZHIHU_RESP)
    )

    resources = await provider.search("三体", 5)
    assert len(resources) == 2
    assert all(r.type is ResourceType.article for r in resources)

    r1 = resources[0]
    assert r1.title == "如何评价《三体》"
    assert r1.author == "张三"
    assert r1.payment is PaymentStatus.free
    assert r1.url == "https://www.zhihu.com/question/1/answer/123456"
    assert r1.extra["voteup_count"] == 5000

    # 第二个是付费（盐选）内容
    r2 = resources[1]
    assert r2.payment is PaymentStatus.paid
    assert "盐选" in r2.payment_note


@respx.mock
async def test_zhihu_requires_zc0(monkeypatch):
    """缺 z_c0 登录态时降级，提示里说明只需配 z_c0（d_c0 自动获取）."""
    from app.providers.base import ProviderError

    monkeypatch.delenv("ZHIHU_COOKIE", raising=False)
    provider = _make(ZhihuProvider)
    # mock /explore 下发游客 d_c0
    respx.get("https://www.zhihu.com/explore").mock(
        return_value=httpx.Response(200, headers={"set-cookie": "d_c0=guest_dc0; Path=/"})
    )
    with pytest.raises(ProviderError, match="z_c0"):
        await provider.search("三体", 5)


@respx.mock
async def test_zhihu_403_raises(monkeypatch):
    from app.providers.base import ProviderError

    monkeypatch.setenv("ZHIHU_COOKIE", "d_c0=test_dc0")
    provider = _make(ZhihuProvider)
    respx.get(url__startswith="https://www.zhihu.com/api/v4/search_v3").mock(
        return_value=httpx.Response(403)
    )
    with pytest.raises(ProviderError):
        await provider.search("三体", 5)


# ---------------------------------------------------------------- wechat（搜狗）


@respx.mock
async def test_wechat_sogou_parse():
    provider = _make(WechatProvider)
    route = respx.get("https://weixin.sogou.com/weixin")
    # 第 1 页返回 2 条，第 2 页起空（原始块数 0 → 停止翻页）
    route.side_effect = [
        httpx.Response(200, text=SOGOU_HTML),
        httpx.Response(200, text="<html></html>"),
    ]

    resources = await provider.search("三体", 10)
    assert len(resources) == 2
    assert all(r.type is ResourceType.article for r in resources)

    r1 = resources[0]
    assert r1.title == "国产「三体」为什么必须这么改"  # 高亮标签已清理
    assert r1.author == "Sir电影"
    assert r1.url.startswith("https://weixin.sogou.com/link?url=")
    assert r1.payment is PaymentStatus.free
    assert r1.publish_date is not None  # timeConvert 时间戳已解析
    assert "创作原点" in r1.extra["excerpt"]

    r2 = resources[1]
    assert r2.title == "《三体》动画版,我是你的催更人"
    assert r2.author == "新周刊"


@respx.mock
async def test_wechat_captcha_degrades():
    from app.providers.base import ProviderError

    provider = _make(WechatProvider)
    respx.get("https://weixin.sogou.com/weixin").mock(
        return_value=httpx.Response(200, text="<html>用户您好，您的访问过于频繁，请输入验证码</html>")
    )
    with pytest.raises(ProviderError, match="验证码"):
        await provider.search("三体", 5)


@respx.mock
async def test_wechat_partial_results_kept_on_captcha():
    """翻页中途被验证码拦截时，保留已抓到的部分结果."""
    provider = _make(WechatProvider)

    route = respx.get("https://weixin.sogou.com/weixin")
    # page=1 正常 10 条，page=2 被拦
    route.side_effect = [
        httpx.Response(200, text=SOGOU_HTML * 5),  # 10 条
        httpx.Response(200, text="用户您好，您的访问过于频繁，请输入验证码"),
    ]

    resources = await provider.search("三体", 50)
    assert len(resources) == 10  # 第 2 页被拦，但前 10 条保留

    # 冷却已启动：再次搜索直接降级（不再发请求）
    with pytest.raises(Exception, match="冷却"):
        await provider.search("三体", 10)
    assert route.call_count == 2  # 冷却期间没有第三次请求
