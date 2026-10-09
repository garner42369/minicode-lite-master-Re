"""Day 27：指标采集的契约测试。"""

from minicode.agent_metrics import AgentMetrics


class TestAgentMetrics:
    def test_record_and_success_rate(self):
        m = AgentMetrics()
        m.record_model_call(100.0)
        m.record_model_call(200.0)
        m.record_tool_result(True)
        m.record_tool_result(False)
        assert m.model_calls == 2
        assert m.tool_calls == 2
        assert m.tool_failures == 1
        assert m.tool_success_rate == 0.5
        assert m.avg_model_ms == 150.0
        print(f"\n指标: {m.to_dict()}")

    def test_no_tools_success_rate_one(self):
        m = AgentMetrics()
        assert m.tool_success_rate == 1.0
        print(f"\n无工具调用成功率: {m.tool_success_rate}")
