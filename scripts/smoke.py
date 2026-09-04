"""端到端冒烟：起服务（或用已运行的）→ 搜 3 个关键词 → 校验响应结构."""

from __future__ import annotations

import asyncio
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
KEYWORDS = ["三体", "郭德纲", "红楼梦"]
REQUIRED_KEYS = {"keyword", "results", "errors", "elapsed_ms"}


def check(cond: bool, msg: str) -> bool:
    print(f"  {'[PASS]' if cond else '[FAIL]'} {msg}")
    return cond


async def main() -> int:
    print(f"冒烟测试 → {BASE}")
    ok = True
    async with httpx.AsyncClient(timeout=30) as client:
        # 0. 服务存活
        try:
            resp = await client.get(f"{BASE}/api/providers")
            ok &= check(resp.status_code == 200, f"/api/providers HTTP {resp.status_code}")
            providers = resp.json().get("active", [])
            print(f"  活跃源: {providers}")
        except Exception as exc:
            print(f"  [FAIL] 服务未启动: {exc}（先运行 uvicorn app.main:app --port 8000）")
            return 1

        # 1. 搜索三类关键词
        for kw in KEYWORDS:
            print(f"--- 搜索「{kw}」")
            resp = await client.get(
                f"{BASE}/api/search", params={"q": kw, "types": "video,audio,book"}
            )
            if not check(resp.status_code == 200, f"HTTP {resp.status_code}"):
                ok = False
                continue
            data = resp.json()
            ok &= check(REQUIRED_KEYS <= set(data), "响应结构完整")
            total = sum(len(v) for v in data["results"].values())
            types_hit = list(data["results"].keys())
            print(f"  结果: {total} 条, 类型: {types_hit}, 耗时 {data['elapsed_ms']}ms")
            if data["errors"]:
                print(f"  (降级源: {data['errors']})")
            ok &= check(total > 0, "至少返回 1 条结果")
            # 抽样校验字段
            for items in data["results"].values():
                for r in items[:1]:
                    has_payment = r.get("payment") in ("free", "paid", "vip", "unknown")
                    has_avail = r.get("availability") in ("available", "unverified", "unavailable")
                    ok &= check(has_payment and has_avail, f"字段完整: {r['title'][:20]}")
                break

        # 2. 单条探测
        print("--- 探测接口")
        resp = await client.post(
            f"{BASE}/api/probe",
            json={
                "resource_id": "smoke:test",
                "url": "https://pan.baidu.com/s/1zzzzzzzzzzzzzzzzzzzzzzz",
                "source": "smoke",
                "pan_type": "baidu",
                "type": "netdisk_share",
            },
        )
        if check(resp.status_code == 200, f"HTTP {resp.status_code}"):
            avail = resp.json().get("availability")
            ok &= check(avail == "unavailable", f"乱构造链接应判 unavailable, 实际 {avail}")

    print("=" * 40)
    print("冒烟结果:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
