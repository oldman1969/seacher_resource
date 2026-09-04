"""单元测试：网盘链接判定 + probe 状态映射 + 资源清洗."""

from app.models import AvailabilityStatus, Resource, ResourceType
from app.netdisk.validators import detect_pan_type
from app.probe import ProbeEngine
from app.config import ProbeConfig


class TestDetectPanType:
    def test_baidu(self):
        assert detect_pan_type("https://pan.baidu.com/s/1abcDEF_-xyz?pwd=1234") == "baidu"

    def test_quark(self):
        assert detect_pan_type("https://pan.quark.cn/s/abc123def") == "quark"

    def test_aliyun(self):
        assert detect_pan_type("https://www.alipan.com/s/xyz789abc") == "aliyun"

    def test_xunlei(self):
        assert detect_pan_type("https://pan.xunlei.com/s/AbC-12345") == "xunlei"

    def test_magnet_none(self):
        # magnet 不在 PAN_PATTERNS 中（由 pansou provider 处理类型）
        assert detect_pan_type("magnet:?xt=urn:btih:abc") is None

    def test_unknown(self):
        assert detect_pan_type("https://example.com/s/abc") is None


class TestProbeStatusMapping:
    def setup_method(self):
        self.engine = ProbeEngine(ProbeConfig())

    def test_2xx_available(self):
        assert self.engine._map_status(200)[0] is AvailabilityStatus.available

    def test_3xx_available(self):
        assert self.engine._map_status(302)[0] is AvailabilityStatus.available

    def test_404_unavailable(self):
        assert self.engine._map_status(404)[0] is AvailabilityStatus.unavailable

    def test_410_unavailable(self):
        assert self.engine._map_status(410)[0] is AvailabilityStatus.unavailable

    def test_403_unverified(self):
        """反爬拦截不代表资源失效."""
        status, detail = self.engine._map_status(403)
        assert status is AvailabilityStatus.unverified
        assert "拦截" in detail

    def test_412_unverified(self):
        assert self.engine._map_status(412)[0] is AvailabilityStatus.unverified

    def test_429_unverified(self):
        assert self.engine._map_status(429)[0] is AvailabilityStatus.unverified

    def test_500_unverified(self):
        assert self.engine._map_status(500)[0] is AvailabilityStatus.unverified


class TestResourceSanitize:
    def test_surrogate_cleaned(self):
        """未配对 surrogate 必须被清除（B 站脏数据会毒化 JSON）."""
        dirty = "标题\ud83d_ABC"
        r = Resource(
            resource_id="t:1", title=dirty, type=ResourceType.video,
            source="t", url="https://example.com",
        )
        assert "\ud83d" not in r.title
        assert "ABC" in r.title

    def test_extra_nested_cleaned(self):
        r = Resource(
            resource_id="t:2", title="ok", type=ResourceType.book,
            source="t", url="https://example.com",
            extra={"desc": "x\ud800y", "tags": ["a\udbff"]},
        )
        assert "\ud800" not in r.extra["desc"]
        assert "\udbff" not in r.extra["tags"][0]

    def test_probe_key(self):
        r = Resource(
            resource_id="t:3", title="", type=ResourceType.audio,
            source="netease", url="https://music.163.com/x",
        )
        assert r.probe_key() == "netease|https://music.163.com/x"
