"""Day 25：CapabilityRegistry 的契约测试。"""

import pytest

from minicode.capability_registry import Capability, CapabilityRegistry


class TestCapabilityRegistry:
    def test_register_and_list(self):
        reg = CapabilityRegistry()
        reg.register(Capability("tools", "tool", "workspace", True, "builtin tools"))
        reg.register(Capability("mcp", "external", "external", False, "mcp tools"))
        assert reg.list_enabled() == ["tools"]
        assert reg.list_all() == ["tools", "mcp"]
        assert reg.has("tools") is True
        assert reg.get("mcp").domain == "external"
        assert reg.get("nope") is None
        print(f"\n启用={reg.list_enabled()}, 全部={reg.list_all()}")

    def test_duplicate_raises(self):
        reg = CapabilityRegistry()
        reg.register(Capability("tools", "tool", "workspace"))
        with pytest.raises(ValueError, match="duplicate"):
            reg.register(Capability("tools", "tool", "workspace"))
        print("\n重复注册抛 ValueError")
