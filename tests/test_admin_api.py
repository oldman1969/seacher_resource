"""单元测试：管理端点（独立 ADMIN_PASSWORD 验证 + 知乎 cookie Web 更新）.

密码模型（两个独立密码）：
- ACCESS_PASSWORD：站点访问密码（谁能看网站）
- ADMIN_PASSWORD：管理密码（谁能改配置）；未设置 = 设置面板锁定
- 管理会话 cookie 只放行 /api/admin/*，不能访问站点其他内容
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """隔离的 .env：把 PROJECT_ROOT 指到临时目录，避免污染真实 .env."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    import app.config as config_mod

    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("ZHIHU_COOKIE", raising=False)
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    return tmp_path


def _client():
    transport = ASGITransport(app=create_app())
    return AsyncClient(transport=transport, base_url="http://test")


async def test_admin_locked_without_admin_password(tmp_env):
    """ADMIN_PASSWORD 未设置：管理端点返回 403（锁定），而非 401（需验证）.
    不设 ACCESS_PASSWORD（本地开发模式），避免站点认证中间件干扰."""
    async with _client() as client:
        r = await client.post("/api/admin/auth", json={"password": "anything"})
        assert r.status_code == 403  # 未配置管理密码

        r2 = await client.get("/api/admin/zhihu-cookie/status")
        assert r2.status_code == 403  # 锁定，不是需要验证


async def test_admin_password_independent_from_site_password(tmp_env, monkeypatch):
    """两密码独立：站点密码过不了管理验证；管理密码只开设置功能不开站点内容."""
    monkeypatch.setenv("ACCESS_PASSWORD", "site_pw")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin_pw")
    async with _client() as client:
        # 用站点密码验证管理 → 拒绝
        r = await client.post("/api/admin/auth", json={"password": "site_pw"})
        assert r.status_code == 401

        # 用管理密码验证 → 通过
        r2 = await client.post("/api/admin/auth", json={"password": "admin_pw"})
        assert r2.status_code == 200

        # admin 会话：能改 cookie、查状态
        r3 = await client.post("/api/admin/zhihu-cookie", json={"cookie": "z_c0=abc"})
        assert r3.status_code == 200
        assert os.environ.get("ZHIHU_COOKIE") == "z_c0=abc"
        r4 = await client.get("/api/admin/zhihu-cookie/status")
        assert r4.json()["configured"] is True

        # .env 持久化
        content = (tmp_env / ".env").read_text(encoding="utf-8")
        assert "ZHIHU_COOKIE=" in content and "z_c0=abc" in content

        # admin cookie 不能访问站点内容（如搜索接口仍需站点登录）
        r5 = await client.get("/api/search?q=test")
        assert r5.status_code == 401


async def test_admin_flow_without_site_password(tmp_env, monkeypatch):
    """站点无密码但管理有密码：站点开放浏览，设置面板仍需管理密码."""
    monkeypatch.setenv("ADMIN_PASSWORD", "admin_pw")
    async with _client() as client:
        # 站点内容可直接访问（静态首页）
        r0 = await client.get("/")
        assert r0.status_code == 200

        # admin 未验证前 401
        r = await client.get("/api/admin/zhihu-cookie/status")
        assert r.status_code == 401

        # 验证后可用
        await client.post("/api/admin/auth", json={"password": "admin_pw"})
        r2 = await client.get("/api/admin/zhihu-cookie/status")
        assert r2.status_code == 200
        assert r2.json()["configured"] is False


async def test_zhihu_cookie_empty_rejected(tmp_env, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "admin_pw")
    async with _client() as client:
        await client.post("/api/admin/auth", json={"password": "admin_pw"})
        r = await client.post("/api/admin/zhihu-cookie", json={"cookie": ""})
        assert r.status_code == 422
