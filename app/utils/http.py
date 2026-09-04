"""共享 AsyncClient 工厂：统一 UA、超时、代理、cookie."""

from __future__ import annotations

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)


def build_client(
    proxy: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> httpx.AsyncClient:
    """创建共享 AsyncClient.

    注意：不带 br 压缩头之外的特殊处理由各 Provider 自行覆盖
    （百度网盘需 Accept-Encoding: identity 绕过假 gzip 头）.
    """
    return httpx.AsyncClient(
        headers={**BASE_HEADERS, **(headers or {})},
        timeout=timeout or DEFAULT_TIMEOUT,
        proxy=proxy,
        follow_redirects=True,
        http2=False,  # 国内多数站点 http2 握手不一致，保守关闭
    )
