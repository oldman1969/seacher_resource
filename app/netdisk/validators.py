"""网盘分享链接有效性判定.

移植自开源项目 fishforks/NetDiskLinkValidator 的已验证逻辑（httpx 异步化）.
百度：GET share 页（Accept-Encoding: identity 绕过假 gzip 头，Phase 0 实测），
      页面特征词判定.
夸克：官方接口 sharepage/token + sharepage/detail.
"""

from __future__ import annotations

import re
import time

import httpx

from app.models import AvailabilityStatus

# 各网盘 URL 正则（源自 NetDiskLinkValidator）
PAN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("baidu", re.compile(r"pan\.baidu\.com/s/([0-9a-zA-Z_-]+)")),
    ("quark", re.compile(r"pan\.quark\.cn/s/([0-9a-f]+)")),
    ("aliyun", re.compile(r"(?:aliyundrive|alipan)\.com/s/([0-9a-zA-Z]+)")),
    ("xunlei", re.compile(r"pan\.xunlei\.com/s/([0-9a-zA-Z_-]+)")),
    ("115", re.compile(r"115\.com/s/([0-9a-zA-Z]+)")),
    ("uc", re.compile(r"drive\.uc\.cn/s/([0-9a-zA-Z]+)")),
    ("tianyi", re.compile(r"cloud\.189\.cn/(?:web/share\?code=|t/)([0-9a-zA-Z]+)")),
    ("123", re.compile(r"123pan\.com/s/([0-9a-zA-Z-]+)")),
    ("pikpak", re.compile(r"pikpak\.com/s/([0-9a-zA-Z_-]+)")),
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

BAIDU_FAIL_MARKERS = [
    "分享的文件已经被取消",
    "分享已过期",
    "你访问的页面不存在",
    "你所访问的页面",
    "啊哦，你来晚了",
]
BAIDU_NEED_PWD_MARKERS = ["请输入提取码", "提取文件", "share/init"]
BAIDU_OK_MARKERS = ["过期时间", "文件列表", "分享名称"]

QUARK_TOKEN_URL = "https://drive-h.quark.cn/1/clouddrive/share/sharepage/token"
QUARK_DETAIL_URL = "https://drive-h.quark.cn/1/clouddrive/share/sharepage/detail"

# 百度限速：全局并发由 probe.py 控制，这里做最小间隔
_baidu_last = 0.0
_BAIDU_MIN_INTERVAL = 2.0


def detect_pan_type(url: str) -> str | None:
    for ptype, pat in PAN_PATTERNS:
        if pat.search(url):
            return ptype
    return None


async def validate_share(
    client: httpx.AsyncClient, url: str, pan_type: str | None = None
) -> tuple[AvailabilityStatus, str]:
    """判定网盘分享链接有效性，返回 (状态, 说明)."""
    ptype = pan_type or detect_pan_type(url)
    try:
        if ptype == "baidu":
            return await _validate_baidu(client, url)
        if ptype == "quark":
            return await _validate_quark(client, url)
        # 其他网盘：通用 HTTP 探测（probe 模块处理）
        return AvailabilityStatus.unverified, f"暂不支持 {ptype} 深度校验"
    except Exception as exc:  # noqa: BLE001
        return AvailabilityStatus.unverified, f"{type(exc).__name__}: {exc}"


async def _validate_baidu(
    client: httpx.AsyncClient, url: str
) -> tuple[AvailabilityStatus, str]:
    global _baidu_last
    # 限速
    wait = _baidu_last + _BAIDU_MIN_INTERVAL - time.monotonic()
    if wait > 0:
        import asyncio

        await asyncio.sleep(wait)
    _baidu_last = time.monotonic()

    # Accept-Encoding: identity —— 百度对脚本请求会返回假 gzip 头（Phase 0 实测）
    resp = await client.get(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Encoding": "identity",
            "Referer": "https://pan.baidu.com/",
        },
        follow_redirects=True,
    )
    text = resp.text
    if any(m in text for m in BAIDU_FAIL_MARKERS):
        return AvailabilityStatus.unavailable, "分享已取消/过期/不存在"
    if resp.status_code == 404:
        return AvailabilityStatus.unavailable, "页面不存在"
    if any(m in text for m in BAIDU_NEED_PWD_MARKERS):
        return AvailabilityStatus.available, "有效（需提取码）"
    if any(m in text for m in BAIDU_OK_MARKERS):
        return AvailabilityStatus.available, "有效"
    return AvailabilityStatus.unverified, f"HTTP {resp.status_code}，无法判定"


async def _validate_quark(
    client: httpx.AsyncClient, url: str
) -> tuple[AvailabilityStatus, str]:
    m = re.search(r"pan\.quark\.cn/s/([0-9a-f]+)", url)
    if not m:
        return AvailabilityStatus.unverified, "无法解析夸克链接"
    pwd_id = m.group(1)

    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Referer": "https://pan.quark.cn/"},
        timeout=httpx.Timeout(5.0, read=8.0),
    ) as qc:
        token_resp = await qc.post(
            QUARK_TOKEN_URL,
            params={"pr": "ucpro", "fr": "pc"},
            json={"pwd_id": pwd_id, "passcode": ""},
        )
        data = token_resp.json()
        code = data.get("code")
        if code != 0:
            msg = data.get("message", "")
            if "PASS_CODE" in str(msg).upper():
                return AvailabilityStatus.available, "有效（需提取码）"
            return AvailabilityStatus.unavailable, f"失效: {msg or code}"
        stoken = (data.get("data") or {}).get("stoken")
        if not stoken:
            return AvailabilityStatus.unverified, "未返回 stoken"

        detail = await qc.get(
            QUARK_DETAIL_URL,
            params={"pr": "ucpro", "fr": "pc", "pwd_id": pwd_id, "stoken": stoken,
                    "pdir_fid": 0, "_page": 1, "_size": 1},
        )
        ddata = detail.json()
        if ddata.get("code") == 0:
            return AvailabilityStatus.available, "有效"
        return AvailabilityStatus.unavailable, f"详情校验失败: {ddata.get('message')}"
