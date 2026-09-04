"""Phase 0 端点实测脚本.

对每个候选数据源发真实请求，输出可用性报告，决定哪些源进入 MVP。
用法: python scripts/probe_sources.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from urllib.parse import urlencode, quote

import httpx

# Windows 控制台编码兜底
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
TIMEOUT = httpx.Timeout(connect=5, read=10, write=10, pool=10)

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"{mark} {name}: {detail}")


async def probe_simple(
    name: str, url: str, check, method: str = "GET", **kwargs
) -> None:
    """通用探测: 发请求, 用 check(resp) -> (ok, detail) 判定."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.request(method, url, **kwargs)
            ok, detail = check(resp)
            record(name, ok, f"HTTP {resp.status_code} | {detail}")
    except Exception as exc:
        record(name, False, f"异常: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 各源探测


async def probe_internet_archive() -> None:
    url = (
        "https://archive.org/advancedsearch.php"
        "?q=mediatype%3A(audio+OR+movies+OR+texts)"
        "&fl%5B%5D=identifier&fl%5B%5D=title&rows=2&page=1&output=json"
    )

    def check(resp: httpx.Response):
        data = resp.json()
        n = data.get("response", {}).get("numFound", -1)
        docs = data.get("response", {}).get("docs", [])
        return (resp.status_code == 200 and n >= 0,
                f"numFound={n}, 首条={docs[0].get('title', '?') if docs else '无'}")

    await probe_simple("Internet Archive", url, check)


async def probe_openlibrary() -> None:
    url = "https://openlibrary.org/search.json?q=" + quote("三体") + "&limit=2&fields=key,title"

    def check(resp: httpx.Response):
        data = resp.json()
        n = data.get("numFound", -1)
        docs = data.get("docs", [])
        return (resp.status_code == 200 and n >= 0,
                f"numFound={n}, 首条={docs[0].get('title', '?') if docs else '无'}")

    await probe_simple("Open Library", url, check)


async def probe_gutenberg() -> None:
    url = "https://gutendex.com/books?search=" + quote("three body")

    def check(resp: httpx.Response):
        data = resp.json()
        n = data.get("count", -1)
        return (resp.status_code == 200 and n >= 0, f"count={n}")

    await probe_simple("Gutenberg (gutendex)", url, check)


async def probe_librivox() -> None:
    url = "https://librivox.org/api/feed/audiobooks?title=" + quote("journey") + "&limit=1&format=json"

    def check(resp: httpx.Response):
        ok = resp.status_code == 200
        detail = f"len={len(resp.text)}"
        if ok:
            try:
                data = resp.json()
                items = data.get("books", [])
                detail += f", books={len(items)}"
            except Exception:
                detail += ", 非JSON"
        return ok, detail

    await probe_simple("LibriVox", url, check)


async def probe_netease() -> None:
    url = "https://music.163.com/api/search/get/web"
    params = {"s": "晴天", "type": 1, "limit": 2, "offset": 0}

    def check(resp: httpx.Response):
        if resp.status_code != 200:
            return False, f"非200"
        data = resp.json()
        code = data.get("code", -1)
        songs = data.get("result", {}).get("songs", [])
        if code == 200 and songs:
            s = songs[0]
            fee = s.get("fee", None)
            vip_tag = s.get("copyright", None)
            return True, (f"code=200, 首条《{s.get('name')}》 "
                          f"fee={fee} (0/8=免费,1=VIP,4=付费), singer={s.get('artists',[{}])[0].get('name')}")
        return False, f"code={code}, songs={len(songs)}"

    await probe_simple(
        "网易云音乐", url, check,
        method="POST", data=params,
        headers={**HEADERS, "Referer": "https://music.163.com/"},
    )


async def probe_douban() -> None:
    # movie suggest 接口（相对最宽松）
    url = "https://movie.douban.com/j/subject_suggest?q=" + quote("三体")

    def check(resp: httpx.Response):
        if resp.status_code != 200:
            return False, "非200(可能被反爬)"
        try:
            data = resp.json()
            return True, f"返回{len(data)}条, 首条={data[0].get('title') if data else '无'}"
        except Exception:
            return False, "非JSON"

    await probe_simple(
        "豆瓣 movie suggest", url, check,
        headers={**HEADERS, "Referer": "https://movie.douban.com/"},
    )


async def probe_ximalaya() -> None:
    url = ("https://www.ximalaya.com/revision/search/main"
           "?kw=" + quote("郭德纲") + "&core=track&page=1&rows=2&device=web")

    def check(resp: httpx.Response):
        if resp.status_code != 200:
            return False, f"非200: {resp.text[:80]}"
        try:
            data = resp.json()
            docs = (data.get("data", {}).get("track", {})
                    .get("docs", []) or data.get("result", {}).get("response", {})
                    .get("docs", []))
            return True, f"返回{len(docs)}条 track"
        except Exception as exc:
            return False, f"解析失败: {exc}"

    await probe_simple("喜马拉雅", url, check)


# ------------------------------------------------------------ Bilibili wbi

MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def get_mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MIXIN_TAB)[:32]


def wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
    mixin = get_mixin_key(img_key + sub_key)
    params = {**params, "wts": int(time.time())}
    params = {k: str(v).replace("'", "").replace('"', "") for k, v in params.items()}
    qs = urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((qs + mixin).encode()).hexdigest()
    return params


async def probe_bilibili() -> None:
    name = "Bilibili (wbi 游客态)"
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            # 1. 拿 buvid cookie
            await client.get("https://www.bilibili.com/")
            buvid = client.cookies.get("buvid3") or client.cookies.get("buvid4")
            if not buvid:
                record(name, False, "未获取到 buvid cookie")
                return
            # 2. 拿 wbi key
            nav = await client.get("https://api.bilibili.com/x/web-interface/nav")
            nav_data = nav.json()
            if nav_data.get("code") != 0:
                record(name, False, f"nav 接口 code={nav_data.get('code')}: {nav_data.get('message')}")
                return
            wbi_img = nav_data["data"]["wbi_img"]
            img_key = wbi_img["img_url"].rsplit("/", 1)[1].split(".")[0]
            sub_key = wbi_img["sub_url"].rsplit("/", 1)[1].split(".")[0]
            # 3. 签名搜索
            params = wbi_sign(
                {"search_type": "video", "keyword": "三体", "page": 1},
                img_key, sub_key,
            )
            search = await client.get(
                "https://api.bilibili.com/x/web-interface/wbi/search/type",
                params=params,
            )
            data = search.json()
            code = data.get("code")
            if code == 0:
                results = data.get("data", {}).get("result", [])
                first = results[0] if results else {}
                record(name, True,
                       f"code=0, 返回{len(results)}条, 首条={first.get('title', '?')[:30]}")
            else:
                record(name, False,
                       f"code={code}: {data.get('message')} (需 SESSDATA?)")
    except Exception as exc:
        record(name, False, f"异常: {type(exc).__name__}: {exc}")


# ------------------------------------------------------------ Google 系连通性

async def probe_google_reachability() -> None:
    """YouTube/Spotify 等国际源在当前网络的连通性(仅 TCP/HTTP 层面)."""
    for name, url in [
        ("googleapis.com 连通性", "https://www.googleapis.com/discovery/v1/apis"),
        ("open.spotify.com 连通性", "https://open.spotify.com/"),
    ]:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(url)
                record(name, True, f"可达, HTTP {resp.status_code}")
        except Exception as exc:
            record(name, False, f"不可达: {type(exc).__name__}")


async def main() -> None:
    print("=" * 64)
    print("Phase 0 端点实测报告  (%s)" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 64)

    probes = [
        probe_internet_archive(),
        probe_openlibrary(),
        probe_gutenberg(),
        probe_librivox(),
        probe_netease(),
        probe_douban(),
        probe_ximalaya(),
        probe_bilibili(),
        probe_google_reachability(),
    ]
    # 顺序执行避免限流误判
    for p in probes:
        await p
        await asyncio.sleep(0.5)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("-" * 64)
    print(f"汇总: {passed}/{len(RESULTS)} 通过")
    print(json.dumps(
        {n: {"ok": ok, "detail": d} for n, ok, d in RESULTS},
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
