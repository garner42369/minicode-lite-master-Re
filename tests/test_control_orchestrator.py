"""Day 29：控制编排器的契约测试。"""

from minicode.control_orchestrator import ControlOrchestrator, Signals


class TestControlOrchestrator:
    def test_context_pressure_compacts(self):
        orch = ControlOrchestrator()
        actions = orch.after_tool(Signals(context_ratio=0.95, tool_error_rate=0.0, repeated_failures=0))
        assert any(a.name == "compact" for a in actions)
        print(f"\n上下文压力: {actions}")

    def test_tool_error_reduces_concurrency(self):
        orch = ControlOrchestrator()
        actions = orch.after_tool(Signals(context_ratio=0.5, tool_error_rate=0.6, repeated_failures=0))
        assert any(a.name == "reduce_concurrency" for a in actions)
        print(f"\n工具错误率: {actions}")

    def test_bounded_healing(self):
        orch = ControlOrchestrator(max_healings=1)
        actions1 = orch.after_tool(Signals(0.5, 0.0, 2))
        actions2 = orch.after_tool(Signals(0.5, 0.0, 2))
        assert any(a.name == "retry_idempotent_request" for a in actions1)
        assert not any(a.name == "retry_idempotent_request" for a in actions2)  # 有界
        print(f"\n有界自愈: {actions1} 然后 {actions2}")

    def test_disabled_zero_side_effect(self):
        orch = ControlOrchestrator(enabled=False)
        assert orch.after_tool(Signals(0.95, 0.6, 2)) == []
        print("\n禁用零副作用")
