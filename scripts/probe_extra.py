"""Phase 0 补充实测: B站wbi修正 / 本地代理检测 / 喜马拉雅换参 / gutendex换词 / 网盘探测."""

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
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
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


async def probe_bilibili_fixed() -> None:
    """修正: 未登录 nav code=-101 但 data.wbi_img 仍存在."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            await client.get("https://www.bilibili.com/")
            nav = await client.get("https://api.bilibili.com/x/web-interface/nav")
            data = nav.json().get("data") or {}
            wbi_img = data.get("wbi_img") or {}
            img_url, sub_url = wbi_img.get("img_url"), wbi_img.get("sub_url")
            if not img_url:
                rec("Bilibili wbi(修正)", False, "nav 无 wbi_img, 需登录")
                return
            img_key = img_url.rsplit("/", 1)[1].split(".")[0]
            sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]
            params = wbi_sign(
                {"search_type": "video", "keyword": "三体", "page": 1}, img_key, sub_key
            )
            search = await client.get(
                "https://api.bilibili.com/x/web-interface/wbi/search/type",
                params=params,
            )
            sd = search.json()
            code = sd.get("code")
            if code == 0:
                results = sd.get("data", {}).get("result", [])
                rec("Bilibili wbi(修正)", True,
                    f"code=0, {len(results)}条, 首条={results[0].get('title','?')[:30] if results else '无'}")
            else:
                rec("Bilibili wbi(修正)", False, f"code={code}: {sd.get('message')}")
    except Exception as exc:
        rec("Bilibili wbi(修正)", False, f"{type(exc).__name__}: {exc}")


async def probe_local_proxy() -> None:
    """检测本机常见代理端口, 若有则用代理重测国际源."""
    ports = [7890, 7897, 1080, 10809, 8888, 10808]
    alive = []
    for port in ports:
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1
            )
            w.close()
            alive.append(port)
        except Exception:
            pass
    if not alive:
        rec("本地代理检测", False, f"常见端口 {ports} 均未监听")
        return
    rec("本地代理检测", True, f"发现端口: {alive}")
    proxy = f"http://127.0.0.1:{alive[0]}"
    for name, url in [
        ("IA(经代理)", "https://archive.org/advancedsearch.php?q=mediatype%3Aaudio&rows=1&output=json"),
        ("OpenLibrary(经代理)", "https://openlibrary.org/search.json?q=test&limit=1&fields=key,title"),
        ("googleapis(经代理)", "https://www.googleapis.com/discovery/v1/apis"),
    ]:
        try:
            async with httpx.AsyncClient(
                headers=HEADERS, timeout=TIMEOUT, proxy=proxy, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                rec(name, resp.status_code == 200, f"HTTP {resp.status_code}")
        except Exception as exc:
            rec(name, False, f"{type(exc).__name__}")


async def probe_ximalaya_params() -> None:
    """喜马拉雅换参重试: core=album / 关键词更常见."""
    urls = [
        ("ximalaya core=album",
         "https://www.ximalaya.com/revision/search/main?kw=" + quote("郭德纲") + "&core=album&page=1&rows=3"),
        ("ximalaya core=track(复测)",
         "https://www.ximalaya.com/revision/search/main?kw=" + quote("三体") + "&core=track&page=1&rows=3"),
        ("ximalaya search/cores",
         "https://www.ximalaya.com/revision/search/cores?kw=" + quote("郭德纲") + "&core=album&page=1&rows=3&device=web&spell=true"),
    ]
    for name, url in urls:
        try:
            async with httpx.AsyncClient(
                headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
            ) as client:
                await client.get("https://www.ximalaya.com/")
                resp = await client.get(url)
                ok = resp.status_code == 200
                detail = f"HTTP {resp.status_code}"
                if ok:
                    try:
                        d = resp.json()
                        doc_album = ((d.get("data") or {}).get("album", {}) or {}).get("docs", [])
                        doc_track = ((d.get("data") or {}).get("track", {}) or {}).get("docs", [])
                        detail += f", album={len(doc_album)}, track={len(doc_track)}"
                        if doc_album:
                            detail += f", 首专辑={doc_album[0].get('title')}"
                        ok = bool(doc_album or doc_track)
                    except Exception:
                        detail += ", 非JSON"
                        ok = False
                rec(name, ok, detail)
        except Exception as exc:
            rec(name, False, f"{type(exc).__name__}: {exc}")
        await asyncio.sleep(1)


async def probe_gutenberg_word() -> None:
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get("https://gutendex.com/books?search=" + quote("journey"))
            d = resp.json()
            rec("gutendex(换词)", d.get("count", 0) > 0, f"count={d.get('count')}")
    except Exception as exc:
        rec("gutendex(换词)", False, f"{type(exc).__name__}: {exc}")


async def probe_baidu_pan() -> None:
    """百度网盘分享页特征验证: 用一个大概率失效的链接."""
    surl = "1aaaaaaaaaaaaaaaaaaaaaaaX"  # 乱构造, 大概率不存在
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(f"https://pan.baidu.com/s/{surl}")
            text = resp.text
            markers = {
                "你访问的页面不存在": "你访问的页面不存在" in text,
                "页面不存在(4)": "页面不存在" in text,
                "分享取消": "分享的文件已经被取消" in text,
            }
            hits = [k for k, v in markers.items() if v]
            rec("百度网盘失效页特征", resp.status_code in (200, 404),
                f"HTTP {resp.status_code}, url={resp.url.path[:40]}, 命中标记={hits or '无'}")
    except Exception as exc:
        rec("百度网盘失效页", False, f"{type(exc).__name__}: {exc}")


async def main() -> None:
    await probe_bilibili_fixed()
    await probe_local_proxy()
    await probe_ximalaya_params()
    await probe_gutenberg_word()
    await probe_baidu_pan()


if __name__ == "__main__":
    asyncio.run(main())
