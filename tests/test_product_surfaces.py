"""Day 25：ProductSnapshot 与 Readiness 的契约测试。"""

from minicode.capability_registry import Capability, CapabilityRegistry
from minicode.config import RuntimeConfig
from minicode.product_surfaces import build_product_snapshot, build_readiness


def _mock_config() -> RuntimeConfig:
    return RuntimeConfig(model="mock")


class TestBuildReadiness:
    def test_ready(self):
        r = build_readiness(True, [])
        assert r.status == "ready"
        assert r.reasons == ()
        print(f"\nready: {r}")

    def test_degraded_on_broken_extension(self):
        r = build_readiness(True, ["demo-mcp"])
        assert r.status == "degraded"
        assert "demo-mcp" in r.reasons[0]
        print(f"\ndegraded: {r}")

    def test_blocked_on_provider_missing(self):
        r = build_readiness(False, [])
        assert r.status == "blocked"
        assert "provider unavailable" in r.reasons[0]
        print(f"\nblocked: {r}")


class TestBuildSnapshot:
    def test_capabilities_match_registry(self, tmp_path, monkeypatch):
        reg = CapabilityRegistry()
        reg.register(Capability("tools", "tool", "workspace", True))
        reg.register(Capability("mcp", "external", "external", False))
        monkeypatch.setattr("minicode.product_surfaces.load_runtime_config", _mock_config)
        snap = build_product_snapshot(tmp_path, reg, skills=[], mcp_servers=[], hooks_stats={})
        assert snap.capabilities == ("tools",)  # 快照与 registry 一致（只含启用）
        assert snap.readiness.status == "ready"
        print(f"\ncapabilities={snap.capabilities}, readiness={snap.readiness}")

    def test_snapshot_has_no_secret(self, tmp_path, monkeypatch):
        reg = CapabilityRegistry()
        monkeypatch.setattr("minicode.product_surfaces.load_runtime_config", _mock_config)
        snap = build_product_snapshot(tmp_path, reg)
        text = str(snap.to_dict())
        assert "api_key" not in text
        assert "MINI_CODE_API_KEY" not in text
        print(f"\n快照键: {list(snap.to_dict().keys())}（无密钥）")

    def test_single_extension_failure_is_degraded_not_blocked(self, tmp_path, monkeypatch):
        # 核心验收：单个扩展失败只降级，不把系统误判为不可用
        reg = CapabilityRegistry()
        reg.register(Capability("tools", "tool", "workspace", True))
        monkeypatch.setattr("minicode.product_surfaces.load_runtime_config", _mock_config)
        mcp_servers = [{"name": "demo", "status": "error", "command": "python"}]
        snap = build_product_snapshot(tmp_path, reg, mcp_servers=mcp_servers)
        assert snap.readiness.status == "degraded"
        print(f"\n单个扩展失败 → {snap.readiness.status}: {snap.readiness.reasons}")
