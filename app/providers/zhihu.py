"""知乎 Provider：搜索文章/回答（需登录态 cookie）.

搜索接口 /api/v4/search_v3，需 x-zse-96 签名（见 utils/zhihu_sign.py）+ d_c0 cookie。
group="social"，前端「知乎/微信」勾选后按需查询（include_social 控制）.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import quote

from app.config import ProviderConfig
from app.models import AvailabilityStatus, PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError
from app.utils.zhihu_sign import sign_request

SEARCH_URL = "https://www.zhihu.com/api/v4/search_v3"

# 清理标题里的 HTML 高亮标签（<em> 等）
_TAG_RE = re.compile(r"<[^>]+>")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# search_v3 里 object.type → 视为可展示的结果类型
_ARTICLE_TYPES = {"answer", "article", "question", "zvideo", "pin", "column", "topic"}


class ZhihuProvider(BaseProvider):
    name = "zhihu"
    supported_types = (ResourceType.article,)
    enabled_by_default = False
    group = "social"

    def __init__(self, client, config: ProviderConfig):
        super().__init__(client, config)
        self.cookie = os.environ.get("ZHIHU_COOKIE", "").strip()
        self.d_c0 = ""  # 缓存 d_c0（设备标识，一次获取长期复用，避免每次搜都访问 /explore 触发风控）
        self.headers = {
            "User-Agent": UA,
            "x-zse-93": "101_3_3.0",
            "x-requested-with": "fetch",
        }
        if self.cookie:
            self.headers["Cookie"] = self.cookie

    async def _ensure_cookie(self) -> None:
        """补全 cookie：d_c0 可自动获取（游客设备标识，/explore 下发），
        z_c0（登录态）必须由用户在 .env 配置——搜索接口强制登录."""
        cookie = os.environ.get("ZHIHU_COOKIE", "").strip().strip("'\"")  # 热读取，去引号
        # 易用性：纯值（不含 =）视为 z_c0 的值直接使用
        if cookie and "=" not in cookie:
            cookie = f"z_c0={cookie}"
        # d_c0：优先 cookie，其次缓存；仅两者都为空时才访问 /explore 获取（之后缓存复用）
        d_c0 = _extract_dc0(cookie) or self.d_c0
        if not d_c0:
            resp = await self.client.get("https://www.zhihu.com/explore")
            sc = resp.headers.get("set-cookie", "")
            m = re.search(r"d_c0=([^;]+)", sc)
            if m:
                d_c0 = m.group(1)
        if d_c0 and "d_c0=" not in cookie:
            cookie = f"{cookie}; d_c0={d_c0}" if cookie else f"d_c0={d_c0}"
        self.cookie = cookie
        self.d_c0 = d_c0
        self.headers["Cookie"] = cookie
        self.z_c0 = _extract_cookie_value(cookie, "z_c0")

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        # 先检查 z_c0（登录态）——这是硬门槛，没配直接给清晰提示，
        # 避免先折腾 d_c0 报网络错误掩盖真正原因
        cookie = os.environ.get("ZHIHU_COOKIE", "").strip().strip("'\"")
        if cookie and "=" not in cookie:
            cookie = f"z_c0={cookie}"
        if not _extract_cookie_value(cookie, "z_c0"):
            raise ProviderError(
                self.name,
                "知乎搜索需登录态：请在 .env 配 ZHIHU_COOKIE（含 z_c0，"
                "浏览器登录知乎后 F12 → Application → Cookies 复制 z_c0 值即可，d_c0 会自动获取）",
            )
        try:
            await self._ensure_cookie()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"获取游客 d_c0 失败: {type(exc).__name__}") from exc
        if not self.d_c0:
            raise ProviderError(self.name, "无法获取 d_c0（知乎不可达或被风控）")
        try:
            # 翻页：知乎单页 limit=20，offset 递增翻页。普通搜索翻 3 页（~60 条），
            # 深度搜索翻 5 页（~100 条，知乎搜索翻页深度有限，再深易被风控）
            max_pages = 5 if limit > 100 else 3
            out: list[Resource] = []
            for page in range(max_pages):
                offset = page * 20
                url = (
                    f"{SEARCH_URL}?t=general&q={quote(keyword)}&correction=1"
                    f"&search_source=Normal&limit=20&offset={offset}"
                )
                signature = sign_request(url, self.d_c0)
                resp = await self.client.get(
                    url, headers={**self.headers, "x-zse-96": signature}
                )
                if resp.status_code != 200:
                    raise ProviderError(self.name, f"HTTP {resp.status_code}（cookie 失效或被风控）")
                data = resp.json()
                raw_items = data.get("data") or []
                batch = self._parse(data, limit - len(out))
                out.extend(batch)
                # 用原始结果数判断是否有下一页（解析会过滤非文章类型，
                # 若用 batch 数会误判「最后一页」提前停止）
                if len(raw_items) < 20 or len(out) >= limit:
                    break  # 原始结果不足 20 条 = 最后一页，或已够
            return out
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    def _parse(self, data: dict, limit: int) -> list[Resource]:
        items = data.get("data") or []
        if isinstance(items, dict):
            items = [items]
        out: list[Resource] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            obj = it.get("object") or it
            if not isinstance(obj, dict):
                continue
            r = self._to_resource(obj)
            if r:
                out.append(r)
            if len(out) >= limit:
                break
        return out

    def _to_resource(self, obj: dict) -> Resource | None:
        otype = obj.get("type", "")
        # 结果有时是 search_result 包装，取其内 object；此处 obj 已是内层
        title = (
            obj.get("title")
            or (obj.get("question") or {}).get("name")
            or obj.get("name")
        )
        if not title:
            return None
        # 清理知乎结果标题里的 <em> 高亮标签等 HTML
        title = _TAG_RE.sub("", str(title)).strip()
        if not title:
            return None
        # 优先按类型构造正确的网页地址（answer 需 question.id、article 走 zhuanlan、
        # question 用单数）；_build_url 处理不了的再兜底转域名
        url = _build_url(otype, obj)
        if not url:
            url = (obj.get("url") or "").replace("api.zhihu.com", "www.zhihu.com", 1)
        if not url:
            return None

        author = obj.get("author") or {}
        author_name = author.get("name") if isinstance(author, dict) else author

        # 付费：知乎盐选专栏/付费咨询等标 paid，普通公开内容 free
        payment = PaymentStatus.free
        note = None
        if obj.get("paid_info") or obj.get("is_paid"):
            payment = PaymentStatus.paid
            note = "盐选/付费内容"

        return Resource(
            resource_id=f"zhihu:{obj.get('id') or url}",
            title=str(title)[:200],
            type=ResourceType.article,
            source=self.name,
            url=url,
            payment=payment,
            payment_note=note,
            availability=AvailabilityStatus.available,  # 公开条目页，恒可访问
            author=str(author_name) if author_name else None,
            cover=None,
            publish_date=_ts_to_date(obj.get("created_time") or obj.get("created")),
            extra={
                "excerpt": (obj.get("excerpt") or "")[:200],
                "voteup_count": obj.get("voteup_count"),
                "comment_count": obj.get("comment_count"),
                "type": otype,
            },
        )


def _extract_cookie_value(cookie: str, key: str) -> str:
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith(f"{key}="):
            return part[len(key) + 1 :].strip()
    return ""


def _extract_dc0(cookie: str) -> str:
    return _extract_cookie_value(cookie, "d_c0")


def _build_url(otype: str, obj: dict) -> str | None:
    oid = obj.get("id")
    if not oid:
        return None
    if otype == "article":
        return f"https://zhuanlan.zhihu.com/p/{oid}"
    if otype == "answer":
        qid = (obj.get("question") or {}).get("id")
        if qid:
            return f"https://www.zhihu.com/question/{qid}/answer/{oid}"
    if otype == "question":
        return f"https://www.zhihu.com/question/{oid}"
    if otype == "zvideo":
        return f"https://www.zhihu.com/zvideo/{oid}"
    if otype in ("pin", "pin_general"):
        return f"https://www.zhihu.com/pin/{oid}"
    if otype == "column":
        return f"https://zhuanlan.zhihu.com/{oid}"
    if otype == "people":
        return f"https://www.zhihu.com/people/{oid}"
    return None


def _ts_to_date(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts)).date()
    except (ValueError, OSError, TypeError):
        return None
