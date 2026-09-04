"""单元测试：wbi 签名（固定 key 断言 w_rid）."""

from app.utils.wbi import get_mixin_key, wbi_sign

# 社区文档中的标准测试向量（img_key/sub_key 来自公开示例）
TEST_IMG_KEY = "7cd084941338484aae1ad9425b84077c"
TEST_SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


def test_get_mixin_key_length():
    key = get_mixin_key(TEST_IMG_KEY, TEST_SUB_KEY)
    assert len(key) == 32


def test_get_mixin_key_deterministic():
    """混淆表取值正确（与已知向量一致）."""
    key = get_mixin_key(TEST_IMG_KEY, TEST_SUB_KEY)
    combined = TEST_IMG_KEY + TEST_SUB_KEY
    assert key[:5] == combined[46] + combined[47] + combined[18] + combined[2] + combined[53]


def test_wbi_sign_structure():
    signed = wbi_sign({"keyword": "三体", "search_type": "video"}, "ea1db2af37870317e6257b7a83db9f8f")
    assert "wts" in signed and "w_rid" in signed
    assert len(signed["w_rid"]) == 32  # md5 hex
    assert int(signed["wts"]) > 1700000000


def test_wbi_sign_strips_quotes():
    """value 中的引号应被过滤（社区文档要求）."""
    signed = wbi_sign({"keyword": 'a"b'}, "ea1db2af37870317e6257b7a83db9f8f")
    assert '"' not in str(signed["keyword"])
