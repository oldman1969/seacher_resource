"""结果排序：免费优先 > 标题相关度 > 热度."""

from __future__ import annotations

import math

from app.models import PaymentStatus, Resource

# 免费且匹配度高的排前面：free=2, unknown=0.5, paid/vip=0
PAYMENT_BONUS = {
    PaymentStatus.free: 2.0,
    PaymentStatus.unknown: 0.5,
    PaymentStatus.paid: 0.0,
    PaymentStatus.vip: 0.0,
}


def relevance(title: str, keyword: str) -> float:
    """标题相关度：完整包含 1.0；按关键词分词部分命中线性衰减；无命中 0.05."""
    if not title or not keyword:
        return 0.0
    t, k = title.lower(), keyword.lower().strip()
    if k in t:
        return 1.0
    # 中文按字、英文按词粗分
    tokens = [k[i : i + 2] for i in range(len(k) - 1)] if len(k) > 2 else [k]
    hits = sum(1 for tok in tokens if tok in t)
    return 0.1 + 0.8 * (hits / max(len(tokens), 1)) if hits else 0.05


def heat(resource: Resource) -> float:
    """热度分：播放量/下载数取 log10 压缩."""
    counts = [
        resource.extra.get("play_count"),
        resource.extra.get("downloads"),
    ]
    valid = [c for c in counts if isinstance(c, (int, float)) and c > 0]
    return math.log10(max(valid[0], 1)) if valid else 0.0


def score(resource: Resource, keyword: str) -> float:
    return (
        relevance(resource.title, keyword) * 3.0
        + heat(resource) * 0.5
        + PAYMENT_BONUS.get(resource.payment, 0.0)
    )


def rank(resources: list[Resource], keyword: str) -> list[Resource]:
    return sorted(resources, key=lambda r: score(r, keyword), reverse=True)
