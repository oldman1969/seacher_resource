"""Bilibili wbi 签名（游客态）.

流程：GET bilibili.com 拿 buvid cookie → nav 接口拿 img/sub key →
mixinKeyEncTab 混淆取 32 位 mixin_key → 参数加 wts 后 MD5 得 w_rid.
参考：SocialSisterYi/bilibili-API-collect
"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urlencode

# 混淆索引表（社区文档维护， historically stable）
MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


def get_mixin_key(img_key: str, sub_key: str) -> str:
    combined = img_key + sub_key
    return "".join(combined[i] for i in MIXIN_TAB)[:32]


def wbi_sign(params: dict, mixin_key: str) -> dict:
    """对参数做 wbi 签名，返回带 wts/w_rid 的完整参数."""
    params = {**params, "wts": int(time.time())}
    # 过滤 value 中的引号（社区文档建议）
    params = {k: str(v).replace("'", "").replace('"', "") for k, v in params.items()}
    qs = urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((qs + mixin_key).encode()).hexdigest()
    return params


class WbiKeys:
    """img/sub key 缓存（有效期约 1 天，进程内缓存即可）."""

    def __init__(self) -> None:
        self._img_key: str | None = None
        self._sub_key: str | None = None

    async def ensure(self, client) -> tuple[str, str]:
        if self._img_key and self._sub_key:
            return self._img_key, self._sub_key
        # 先访问主页确保有 buvid cookie（游客态必需）
        await client.get("https://www.bilibili.com/")
        resp = await client.get(NAV_URL)
        data = (resp.json().get("data") or {})
        wbi_img = data.get("wbi_img") or {}
        img_url = wbi_img.get("img_url") or ""
        sub_url = wbi_img.get("sub_url") or ""
        if not img_url:
            raise RuntimeError("nav 接口未返回 wbi_img（可能被风控，需配置 BILI_SESSDATA）")
        self._img_key = img_url.rsplit("/", 1)[1].split(".")[0]
        self._sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]
        return self._img_key, self._sub_key

    async def sign(self, client, params: dict) -> dict:
        img_key, sub_key = await self.ensure(client)
        return wbi_sign(params, get_mixin_key(img_key, sub_key))


# 进程级共享缓存（所有 Bilibili 请求复用同一组 key）
shared_wbi_keys = WbiKeys()
