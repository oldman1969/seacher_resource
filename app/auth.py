"""访问密码中间件 + 登录端点（可选）.

在 .env 设置 ACCESS_PASSWORD 后，访问站点会先进入一个只有「密码框」的登录页，
输入正确密码后种一个 HttpOnly 的签名 cookie，之后即可正常访问；留空则完全放行。
仅校验密码，无用户名。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from dotenv import load_dotenv
from fastapi import APIRouter, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app.config import PROJECT_ROOT

# 不依赖 lifespan 时序，模块加载即读 .env
load_dotenv(PROJECT_ROOT / ".env")

COOKIE_NAME = "seacher_auth"
ADMIN_COOKIE_NAME = "seacher_admin"
_TTL_SECONDS = 60 * 60 * 24 * 30  # 站点 cookie 有效期 30 天
_ADMIN_TTL_SECONDS = 2 * 60 * 60  # 管理会话 2 小时
# 免站点登录的路径：登录端点本身 + 管理密码验证（验证管理密码即管理身份证明）
_AUTH_PATHS = {"/auth", "/auth/logout", "/api/admin/auth"}

auth_router = APIRouter()


def _password() -> str | None:
    pw = os.environ.get("ACCESS_PASSWORD", "").strip()
    return pw or None


def _admin_password() -> str | None:
    """管理密码（设置面板验证）。独立于站点访问密码；未设置 = 设置功能锁定."""
    pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    return pw or None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(message: str, password: str) -> bytes:
    return hmac.new(
        password.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).digest()


def _make_token(password: str, ttl: int = _TTL_SECONDS) -> str:
    """签发 token：`{过期时间}.{HMAC(过期时间)}`，密钥为密码，不落盘、无状态."""
    exp = str(int(time.time()) + ttl)
    return f"{exp}.{_b64url(_sign(exp, password))}"


def _verify_token(token: str, password: str) -> bool:
    try:
        exp, sig = token.split(".", 1)
        if int(exp) < int(time.time()):
            return False
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(sig, _b64url(_sign(exp, password)))


def _is_authenticated(request: Request) -> bool:
    """站点访问认证：站点 cookie 有效，或（仅限 /api/admin/* 路径）管理 cookie 有效.

    管理 cookie 不能放大为整站访问——ADMIN_PASSWORD 持有者只进设置功能.
    """
    password = _password()
    if password is None:
        return True
    token = request.cookies.get(COOKIE_NAME)
    if bool(token) and _verify_token(token, password):
        return True
    if request.url.path.startswith("/api/admin"):
        admin_pw = _admin_password()
        admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
        if admin_pw and bool(admin_token) and _verify_token(admin_token, admin_pw):
            return True
    return False


def _is_api(path: str) -> bool:
    return (
        path.startswith("/api")
        or path in ("/docs", "/openapi.json", "/redoc")
        or path.startswith("/docs/")
        or path.startswith("/redoc/")
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _AUTH_PATHS:
            return await call_next(request)
        if _is_authenticated(request):
            return await call_next(request)
        # 未认证：页面请求给登录页，API 给 401
        if request.method == "GET" and not _is_api(request.url.path):
            return HTMLResponse(LOGIN_PAGE)
        return Response('{"detail":"未认证"}', status_code=401, media_type="application/json")


@auth_router.post("/auth")
async def login(body: dict, response: Response):
    """校验密码，成功后种签名 cookie."""
    password = _password()
    if password is None:
        return {"ok": True}
    provided = str(body.get("password") or "")
    if not hmac.compare_digest(provided, password):
        response.status_code = 401
        return {"detail": "密码错误"}
    response.set_cookie(
        COOKIE_NAME,
        _make_token(password),
        max_age=_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@auth_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


LOGIN_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>访问验证</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0f1115; color: #e6e8eb;
    font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  }
  .card {
    width: 320px; background: #1a1d23; border: 1px solid #2a2e36;
    border-radius: 14px; padding: 34px 28px; text-align: center;
  }
  .card h1 { font-size: 18px; font-weight: 600; margin-bottom: 6px; }
  .card .hint { font-size: 13px; color: #9aa3ad; margin-bottom: 22px; }
  input[type=password] {
    width: 100%; padding: 11px 14px; border: 1px solid #343a44; border-radius: 8px;
    background: #101318; color: #e6e8eb; font-size: 14px; outline: none;
  }
  input[type=password]:focus { border-color: #4c8dff; }
  button {
    width: 100%; margin-top: 14px; padding: 11px; border: 0; border-radius: 8px;
    background: #2f6fed; color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
  }
  button:hover { background: #2a64d6; }
  .err { min-height: 18px; margin-top: 12px; font-size: 13px; color: #ff6b6b; }
</style>
</head>
<body>
  <form class="card" id="f">
    <h1>🔒 访问验证</h1>
    <p class="hint">请输入访问密码</p>
    <input type="password" id="pw" autocomplete="current-password" placeholder="密码" autofocus>
    <button type="submit">进入</button>
    <p class="err" id="err"></p>
  </form>
  <script>
    document.getElementById('f').addEventListener('submit', async function (e) {
      e.preventDefault();
      var pw = document.getElementById('pw').value;
      var err = document.getElementById('err');
      err.textContent = '';
      try {
        var r = await fetch('/auth', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: pw })
        });
        if (r.ok) { location.href = '/'; }
        else { err.textContent = '密码错误'; }
      } catch (_) { err.textContent = '请求失败，请重试'; }
    });
  </script>
</body>
</html>
"""
