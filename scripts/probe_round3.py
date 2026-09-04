"""第三轮: B站0条问题 / 百度br解压 / 迅雷/磁力相关连通性."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from urllib.parse import urlencode, quote

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# 不请求 br 压缩, 避免 httpx 缺 brotli 时的解压错误
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}
TIMEOUT = httpx.Timeout(connect=5, read=10, write=10, pool=10)


def rec(name: str, ok: bool, detail: str) -> None:
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}: {detail}")


MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
    orig = "".join((img_key + sub_key)[i] for i in MIXIN_TAB)[:32]
    params = {**params, "wts": int(time.time())}
    qs = urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((qs + orig).encode()).hexdigest()
    return params


async def get_wbi_keys(client: httpx.AsyncClient) -> tuple[str, str] | None:
    nav = await client.get("https://api.bilibili.com/x/web-interface/nav")
    data = nav.json().get("data") or {}
    wbi_img = data.get("wbi_img") or {}
    img_url, sub_url = wbi_img.get("img_url"), wbi_img.get("sub_url")
    if not img_url:
        return None
    return (
        img_url.rsplit("/", 1)[1].split(".")[0],
        sub_url.rsplit("/", 1)[1].split(".")[0],
    )


async def probe_bilibili_variants() -> None:
    try:
        async with httpx.AsyncClient(
            headers={**HEADERS, "Referer": "https://www.bilibili.com/"},
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            await client.get("https://www.bilibili.com/")
            keys = await get_wbi_keys(client)
            if not keys:
                rec("B站 wbi keys", False, "获取失败")
                return
            img_key, sub_key = keys
            for kw in ["三体", "美食", "python"]:
                params = wbi_sign(
                    {"search_type": "video", "keyword": kw, "page": 1},
                    img_key, sub_key,
                )
                sd = (await client.get(
                    "https://api.bilibili.com/x/web-interface/wbi/search/type",
                    params=params,
                )).json()
                code = sd.get("code")
                n = len(sd.get("data", {}).get("result", [])) if code == 0 else -1
                rec(f"B站搜索[{kw}]", code == 0 and n > 0,
                    f"code={code}, {n}条"
                    + (f", msg={sd.get('message')}" if code != 0 else ""))
                await asyncio.sleep(1.5)
    except Exception as exc:
        rec("B站搜索", False, f"{type(exc).__name__}: {exc}")


async def probe_baidu_pan_fixed() -> None:
    surl = "1aaaaaaaaaaaaaaaaaaaaaaaX"
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(f"https://pan.baidu.com/s/{surl}")
            text = resp.text
            hits = [m for m in ["你访问的页面不存在", "分享的文件已经被取消",
                                "分享已过期", "请输入提取码"] if m in text]
            rec("百度网盘失效页(br修复)", bool(hits),
                f"HTTP {resp.status_code}, 最终url={str(resp.url)[:60]}, 命中={hits}")
    except Exception as exc:
        rec("百度网盘(br修复)", False, f"{type(exc).__name__}: {exc}")


async def probe_pansou_registry() -> None:
    """ghcr.io 可达性(PanSou 镜像源)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
            resp = await client.get(
                "https://ghcr.io/v2/fish2018/pansou/tags/list"
            )
            rec("ghcr.io(PanSou镜像)", resp.status_code in (200, 401, 403),
                f"HTTP {resp.status_code} (401/403=需token但registry可达)")
    except Exception as exc:
        rec("ghcr.io(PanSou镜像)", False, f"{type(exc).__name__}")


async def main() -> None:
    await probe_bilibili_variants()
    await probe_baidu_pan_fixed()
    await probe_pansou_registry()


if __name__ == "__main__":
    asyncio.run(main())
