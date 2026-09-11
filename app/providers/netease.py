"""网易云音乐 Provider：直连 web 搜索接口（无需登录）.

Phase 0 实测：POST music.163.com/api/search/get/web 可用，
fee 字段付费判定极佳：0/8=免费, 1=VIP, 4=购买专辑.
"""

from __future__ import annotations

from app.config import ProviderConfig
from app.models import PaymentStatus, Resource, ResourceType
from app.providers.base import BaseProvider, ProviderError

SEARCH_URL = "https://music.163.com/api/search/get/web"

# fee: 0=免费, 1=VIP, 4=购买专辑, 8=免费(低音质可听)
FEE_MAP = {
    0: (PaymentStatus.free, None),
    8: (PaymentStatus.free, "免费（低音质）"),
    1: (PaymentStatus.vip, "VIP 专享"),
    4: (PaymentStatus.paid, "需购买专辑"),
}


class NeteaseProvider(BaseProvider):
    name = "netease"
    supported_types = (ResourceType.audio,)
    enabled_by_default = True

    async def search(self, keyword: str, limit: int = 10) -> list[Resource]:
        try:
            # 网易云搜索接口 limit 上限 100（超出返回 400），钳制到安全范围
            limit = min(limit, 100)
            resp = await self.client.post(
                SEARCH_URL,
                data={"s": keyword, "type": 1, "limit": limit, "offset": 0},
                headers={"Referer": "https://music.163.com/"},
            )
            data = resp.json()
            code = data.get("code", -1)
            songs = (data.get("result") or {}).get("songs") or []
            if code != 200 or not songs:
                if code != 200:
                    raise ProviderError(self.name, f"code={code}")
                return []

            out: list[Resource] = []
            for s in songs[:limit]:
                fee = s.get("fee")
                payment, note = FEE_MAP.get(fee, (PaymentStatus.unknown, None))
                artists = ", ".join(a.get("name", "") for a in s.get("artists") or [])
                album = (s.get("album") or {}).get("name")
                out.append(Resource(
                    resource_id=f"netease:{s.get('id')}",
                    title=s.get("name", ""),
                    type=ResourceType.audio,
                    source=self.name,
                    url=f"https://music.163.com/#/song?id={s.get('id')}",
                    payment=payment,
                    payment_note=note,
                    author=artists or None,
                    extra={
                        "album": album,
                        "duration_s": (s.get("duration") or 0) // 1000,
                    },
                ))
            return out
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc
