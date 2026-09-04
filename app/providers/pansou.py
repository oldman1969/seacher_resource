"""PanSou Provider：自托管网盘/磁力聚合搜索（sidecar 容器）.

PanSou (github.com/fish2018/pansou) 自部署后提供 POST /api/search {"kw": ...}，
结果自带网盘类型分类（baidu/quark/aliyun/xunlei/115/uc/magnet/ed2k...）.
响应字段宽松解析：PanSou 版本迭代字段可能有出入，以部署实例实测为准.
"""

from __future__ import annotations

import re

from app.config import ProviderConfig
from app.models import PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

SEARCH_PATH = "/api/search"

# PanSou 返回的 type → ResourceType + pan_type
PAN_TYPE_MAP: dict[str, ResourceType] = {
    "baidu": ResourceType.netdisk_share,
    "aliyun": ResourceType.netdisk_share,
    "quark": ResourceType.netdisk_share,
    "uc": ResourceType.netdisk_share,
    "xunlei": ResourceType.netdisk_share,
    "115": ResourceType.netdisk_share,
    "tianyi": ResourceType.netdisk_share,
    "mobile": ResourceType.netdisk_share,
    "123": ResourceType.netdisk_share,
    "pikpak": ResourceType.netdisk_share,
    "others": ResourceType.netdisk_share,
    "magnet": ResourceType.magnet,
    "ed2k": ResourceType.magnet,
}

# 提取码正则（源自 pansou 插件生态的通用模式）
PWD_RE = re.compile(r"(?:提取码|访问码|密码|pwd|code)\s*[:=：]?\s*([0-9a-zA-Z]{4,8})")


# 常见文件扩展名：网盘搜索场景用户常带扩展名搜（如 "斩神.txt"），
# 但搜索站的分词对整串精确匹配，带扩展名往往 0 结果 → 自动降级为去扩展名重搜
_FILE_EXT_RE = re.compile(
    r"\.(txt|pdf|epub|mobi|azw3?|docx?|xlsx?|pptx?|zip|rar|7z|tar|gz"
    r"|mkv|mp4|avi|rmvb|mov|iso|mp3|flac|wav|apk|exe|dmg)\s*$",
    re.IGNORECASE,
)


class PanSouProvider(BaseProvider):
    name = "pansou"
    supported_types = (ResourceType.netdisk_share, ResourceType.magnet)
    enabled_by_default = True

    def __init__(self, client, config: ProviderConfig):
        super().__init__(client, config)
        if not config.base_url:
            raise ProviderError(self.name, "未配置 base_url（docker-compose 中 pansou 服务地址）")
        self.base_url = config.base_url.rstrip("/")

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            resources = await self._do_search(keyword, limit)
            if not resources:
                # 冷词降级：去掉文件扩展名重搜（"斩神.txt" → "斩神"）
                m = _FILE_EXT_RE.search(keyword.strip())
                if m:
                    base = keyword.strip()[: m.start()].strip()
                    if base:
                        resources = await self._do_search(base, limit)
            return resources
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    async def _do_search(self, keyword: str, limit: int) -> list[Resource]:
        resp = await self.client.post(
            f"{self.base_url}{SEARCH_PATH}",
            json={"kw": keyword},
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"HTTP {resp.status_code}（PanSou 服务未启动？）")
        data = resp.json()
        return self._to_resources(data, limit)

    @staticmethod
    def _extract_items(data) -> list[dict]:
        """宽松提取结果列表：兼容 PanSou 各版本响应结构."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "results", "list", "items"):
                v = data.get(key)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    # 可能按类型分组 {baidu: [...], quark: [...]}
                    flat: list[dict] = []
                    for sub in v.values():
                        if isinstance(sub, list):
                            flat.extend(sub)
                    if flat:
                        return flat
        return []

    def _to_resources(self, data: dict, limit: int) -> list[Resource]:
        """解析 PanSou 响应：data.merged_by_type = {pan_type: [item]}.

        item 字段（实测 2026-09）：{url, password, note, datetime, source}.
        按网盘类型轮询取样（round-robin）：避免条数多的类型（如 quark）
        填满 limit 把其他类型（baidu/aliyun/115...）挤掉.
        """
        payload = data.get("data") if isinstance(data, dict) else None
        merged = None
        if isinstance(payload, dict):
            merged = payload.get("merged_by_type")
        if not isinstance(merged, dict):
            # 旧版本兼容：平铺列表
            return self._to_resources_flat(self._extract_items(data), limit)

        # 先全部转为 Resource（带去重），再轮询取样
        by_type: list[list[Resource]] = []
        seen_ids: set[str] = set()
        for pan_type, items in merged.items():
            group: list[Resource] = []
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                r = self._item_to_resource(it, i, pan_type)
                if r and r.resource_id not in seen_ids:
                    seen_ids.add(r.resource_id)
                    group.append(r)
            if group:
                by_type.append(group)

        out: list[Resource] = []
        while len(out) < limit and by_type:
            # 每轮从每个类型各取一条，直至凑满 limit
            still_alive: list[list[Resource]] = []
            for group in by_type:
                if len(out) >= limit:
                    still_alive.append(group)
                    continue
                out.append(group.pop(0))
                if group:
                    still_alive.append(group)
            by_type = still_alive
        return out

    def _to_resources_flat(self, items: list[dict], limit: int) -> list[Resource]:
        out: list[Resource] = []
        for i, it in enumerate(items):
            if isinstance(it, dict):
                r = self._item_to_resource(it, i, it.get("type"))
                if r:
                    out.append(r)
            if len(out) >= limit:
                break
        return out

    def _item_to_resource(
        self, it: dict, idx: int, pan_type: str | None
    ) -> Resource | None:
        url = it.get("url") or it.get("link") or ""
        title = (it.get("note") or it.get("name") or it.get("title") or "").strip()
        if not url or not title:
            return None
        # 部分插件数据是 GBK mojibake / 含未配对 surrogate：清洗 + 尝试修复
        title = _fix_mojibake(
            title.encode("utf-8", "ignore").decode("utf-8", "ignore")
        )
        if not title.strip():
            return None
        pan_type = (pan_type or "").lower()
        if not pan_type:
            pan_type = _guess_pan_type(url)
        rtype = PAN_TYPE_MAP.get(pan_type, ResourceType.netdisk_share)
        pwd = it.get("password") or it.get("pwd")
        if rtype is ResourceType.netdisk_share and not pwd:
            m = PWD_RE.search(title)
            pwd = m.group(1) if m else None
        return Resource(
            resource_id=f"pansou:{_id_of(url, idx)}",
            title=title[:200],
            type=rtype,
            source=self.name,
            url=url,
            payment=PaymentStatus.free,       # 网盘分享/磁力本身不收费
            payment_note=None,
            # 磁力不做 DHT 探测、网盘链接由 probe 模块验证，默认 unverified
            pan_type=pan_type or None,
            password=pwd or None,
            cover=None,
            author=(it.get("source") or "").replace("plugin:", "") or None,
            publish_date=_parse_dt(it.get("datetime")),
            extra={
                "size": it.get("size") or it.get("fileSize"),
                "plugin": it.get("source"),
            },
        )

    async def health_check(self) -> bool:
        try:
            resp = await self.client.get(f"{self.base_url}/", timeout=5.0)
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False


def _guess_pan_type(url: str) -> str:
    u = url.lower()
    if u.startswith("magnet:"):
        return "magnet"
    if u.startswith("ed2k:"):
        return "ed2k"
    domains = {
        "pan.baidu.com": "baidu",
        "aliyundrive": "aliyun",
        "alipan.com": "aliyun",
        "pan.quark.cn": "quark",
        "pan.xunlei.com": "xunlei",
        "115.com": "115",
        "drive.uc.cn": "uc",
        "cloud.189.cn": "tianyi",
        "123pan.com": "123",
        "pikpak.com": "pikpak",
    }
    for domain, ptype in domains.items():
        if domain in u:
            return ptype
    return "others"


def _id_of(url: str, idx: int) -> str:
    """从 URL 提取稳定 ID，提不出则用序号."""
    m = re.search(r"/s/([0-9a-zA-Z_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"btih:([0-9a-fA-F]+)", url)
    if m:
        return m.group(1).lower()
    return f"item{idx}"


def _fix_mojibake(text: str) -> str:
    """修复 PanSou 部分插件的 GBK mojibake（UTF-8 字节被按 GBK 解码存入 JSON）.

    原理：mojibake 文本 encode('gbk') 可还原出原 UTF-8 字节流，再按 utf-8
    解码即得原文（个别丢失字节成 U+FFFD，清除即可）；正常中文的 GBK 字节
    几乎不构成合法 UTF-8 序列，解码后替换符占比高，不满足采用条件.
    """
    if not text:
        return text
    try:
        raw = text.encode("gbk")
    except UnicodeEncodeError:
        return text  # 含 GBK 区外字符（emoji 等），不是 mojibake
    fixed = raw.decode("utf-8", errors="replace")

    def n_cjk(s: str) -> int:
        return sum(1 for c in s if "一" <= c <= "鿿")

    bad_ratio = fixed.count("�") / max(len(fixed), 1)
    # 采用条件：替换符少（确实是 UTF-8 字节流）且中文量约保留 2/3 以上
    # （UTF-8 中文 3 字节按 GBK 解码成 1.5 字符，修复后 CJK 数天然是原文 2/3；
    #   系数取 0.5 留余量，同时防误伤「短中文+长 ASCII」的正常文本）
    if bad_ratio < 0.3 and n_cjk(fixed) >= max(n_cjk(text) * 0.5, 2):
        return fixed.replace("�", " ").strip()
    return text


def _parse_dt(dt: str | None):
    """解析 PanSou 的 ISO8601 时间（如 2026-08-05T01:53:02Z）."""
    if not dt:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00")).date()
    except ValueError:
        return None
