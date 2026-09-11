"""微信 Provider：搜狗微信搜索（type=2 搜文章，免登录免 sidecar）.

直接解析 weixin.sogou.com 搜索结果 HTML。文章链接是搜狗跳转链接
（/link?url=...，含 token），用户点击时 302 到 mp.weixin.qq.com 原文——
正是「搜索 → 跳转看源文章」场景，无需 wechat-download-api 等重型 sidecar.

搜狗反爬较严：全局节流（≥3s 间隔）+ 验证码检测（被拦时降级报错，不硬闯）.
group="social"，前端「知乎/微信」勾选后按需查询.
"""

from __future__ import annotations

import asyncio
import html
import random
import re
import time
from datetime import datetime
from urllib.parse import quote

from app.config import ProviderConfig
from app.models import AvailabilityStatus, PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

SEARCH_URL = "https://weixin.sogou.com/weixin"

# 全局节流：搜狗对高频访问风控敏感。
# 随机间隔（而非固定值）避免固定节奏被反爬识别为机器行为；
# 均值 ~3s（实测搜狗反爬严，下限太低风险高，故 2~4s 而非 1~3s）。
_last_call = 0.0
_MIN_INTERVAL = 2.0
_MAX_INTERVAL = 4.0
# 被验证码拦截后的全局冷却期（秒）：期间直接降级，避免继续触发风控
_COOLDOWN_SECONDS = 600.0
_cooldown_until = 0.0


_TITLE_RE = re.compile(r'<a[^>]*uigs="article_title_\d+"[^>]*>(.*?)</a>', re.S)
_LINK_RE = re.compile(r'href="(/link\?url=[^"]+)"')  # 搜狗文章跳转链接（不依赖属性顺序）
_SUMMARY_RE = re.compile(r'<p class="txt-info"[^>]*>(.*?)</p>', re.S)
_ACCOUNT_RE = re.compile(r'<span class="all-time-y2">(.*?)</span>', re.S)
_TS_RE = re.compile(r"timeConvert\('(\d+)'\)")
_TAG_RE = re.compile(r"<[^>]+>")
_CAPTCHA_MARKERS = ("antispider", "请输入验证码", "用户您好，您的访问过于频繁")


async def _throttle() -> None:
    global _last_call
    interval = random.uniform(_MIN_INTERVAL, _MAX_INTERVAL)
    wait = _last_call + interval - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call = time.monotonic()


def _clean(text: str) -> str:
    """去 HTML 标签（含搜狗的 <em><!--red_beg--> 高亮注释）+ 实体解码."""
    return html.unescape(_TAG_RE.sub("", text)).strip()


class WechatProvider(BaseProvider):
    name = "wechat"
    supported_types = (ResourceType.article,)
    enabled_by_default = False
    group = "social"
    # 翻页抓取耗时：普通 5 页×2~4s≈15s，深度 10 页≈30s，需放宽单源超时
    deadline = 45.0

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        global _cooldown_until
        try:
            if time.monotonic() < _cooldown_until:
                raise ProviderError(
                    self.name, "搜狗冷却中（此前被验证码拦截，约 10 分钟后自动恢复）"
                )
            # 翻页抓取：每页至多 10 条；搜狗实际最多翻约 10 页（"约 N 条"是虚标，
            # 页数超 10 后返回 0 块）。以「原始块数为 0」判最后一页，而非「本页<10 条」
            # 普通搜索（limit≤100）翻 5 页约 50 条/15s；深度搜索（limit>100）翻 10 页约 100 条/30s
            max_pages = 10 if limit > 100 else 5
            out: list[Resource] = []
            for page in range(1, max_pages + 1):
                try:
                    batch, raw_count = await self._search_page(keyword, limit - len(out), page)
                except ProviderError as exc:
                    if "验证码" in exc.reason:
                        _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
                        if out:
                            break  # 已有结果：保留部分结果，停止翻页
                    raise
                out.extend(batch)
                if raw_count == 0 or len(out) >= limit:
                    break  # 原始块数为 0 = 最后一页
            return out[:limit]
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    async def _search_page(self, keyword: str, limit: int, page: int) -> tuple[list[Resource], int]:
        await _throttle()
        resp = await self.client.get(
            SEARCH_URL,
            params={"type": 2, "query": keyword, "page": page},
            headers={"Referer": "https://weixin.sogou.com/"},
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"HTTP {resp.status_code}")
        text = resp.text
        if any(m in text for m in _CAPTCHA_MARKERS) or "antispider" in str(resp.url):
            raise ProviderError(self.name, "被搜狗验证码拦截（稍后重试或降低频率）")
        resources = self._parse(text, limit)
        raw_count = len(text.split('<div class="txt-box">')) - 1
        return resources, raw_count

    def _parse(self, page: str, limit: int) -> list[Resource]:
        # 以 txt-box 为锚点分块（避免依赖外层 div 嵌套层数，搜狗改版容错更高）
        blocks = page.split('<div class="txt-box">')[1:]
        out: list[Resource] = []
        for i, block in enumerate(blocks):
            if len(out) >= limit:
                break
            title_m = _TITLE_RE.search(block)
            link_m = _LINK_RE.search(block)
            if not title_m or not link_m:
                continue
            title = _clean(title_m.group(1))
            if not title:
                continue
            # 相对跳转链接 → 绝对（用户点击时搜狗 302 到微信原文；链接含 token 有时效）
            href = html.unescape(link_m.group(1))
            url = href if href.startswith("http") else f"https://weixin.sogou.com{href}"

            summary_m = _SUMMARY_RE.search(block)
            account_m = _ACCOUNT_RE.search(block)
            ts_m = _TS_RE.search(block)

            out.append(Resource(
                resource_id=f"wechat:{_link_key(url, i)}",
                title=title[:200],
                type=ResourceType.article,
                source=self.name,
                url=url,
                payment=PaymentStatus.free,                  # 公众号文章免费
                availability=AvailabilityStatus.available,  # 跳转链接当下有效
                author=_clean(account_m.group(1)) if account_m else None,
                cover=None,
                publish_date=_ts_to_date(ts_m.group(1)) if ts_m else None,
                extra={
                    "excerpt": _clean(summary_m.group(1))[:200] if summary_m else None,
                    "note": "搜狗跳转链接，点击打开微信原文",
                },
            ))
        return out


def _link_key(url: str, idx: int) -> str:
    """从搜狗跳转链接提取稳定键（url 参数很长，取其 hash 片段）."""
    import hashlib

    return hashlib.md5(url.encode()).hexdigest()[:16] or f"item{idx}"


def _ts_to_date(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts)).date()
    except (ValueError, OSError):
        return None
