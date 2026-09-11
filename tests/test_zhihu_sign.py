"""单元测试：知乎 x-zse-96 签名算法（格式/确定性/pathname 提取）.

注：签名算法的最终正确性需知乎登录 cookie 实测确认（本测试只验证结构性质，
不依赖外部 JS 参考向量）。
"""

from app.utils.zhihu_sign import (
    ALPHABET,
    _custom_encode,
    _encrypt_zse_v4,
    _extract_pathname,
    sign_request,
)


def test_sign_prefix_and_deterministic():
    url = "https://www.zhihu.com/api/v4/search_v3?t=general&q=test"
    s1 = sign_request(url, "d_c0_abc")
    s2 = sign_request(url, "d_c0_abc")
    assert s1.startswith("2.0_")
    assert s1 == s2, "同输入必须同输出（确定性）"


def test_sign_differs_with_dc0():
    url = "https://www.zhihu.com/api/v4/search_v3?t=general&q=test"
    assert sign_request(url, "d_c0_a") != sign_request(url, "d_c0_b")


def test_encrypt_length_and_charset():
    out = _encrypt_zse_v4("e3b0c44298fc1c149afbf4c8996fb924")
    assert len(out) == 64
    assert all(c in ALPHABET for c in out), "输出字符必须都在自定义字母表内"


def test_custom_encode_charset():
    out = _custom_encode(b"\x00" * 16)
    assert all(c in ALPHABET for c in out)


def test_extract_pathname_includes_query():
    url = "https://www.zhihu.com/api/v4/search_v3?t=general&q=%E4%B8%89%E4%BD%93"
    p = _extract_pathname(url)
    assert p == "/api/v4/search_v3?t=general&q=%E4%B8%89%E4%BD%93"


def test_empty_dc0_still_signs():
    # d_c0 为空字符串时也能生成签名（保留在 signSource 末尾）
    s = sign_request("https://www.zhihu.com/api/v4/search_v3", "")
    assert s.startswith("2.0_")
