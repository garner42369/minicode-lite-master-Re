"""Day 27：模型路由（纯函数评分）的契约测试。"""

import pytest

from minicode.agent_router import (
    MODEL_PROFILES,
    DecisionAudit,
    RoutingSignal,
    choose_model,
    extract_complexity,
)
from minicode.intent_parser import Intent


class TestChooseModel:
    def test_complex_task_chooses_strong(self):
        decision = choose_model(
            MODEL_PROFILES,
            RoutingSignal(complexity=0.85, budget_pressure=0.0, latency_pressure=0.0, requires_long_context=True),
            set(),
        )
        assert decision.model == "deepseek-v4-pro"
        print(f"\n复杂任务选: {decision}")

    def test_simple_task_chooses_cheap(self):
        decision = choose_model(
            MODEL_PROFILES,
            RoutingSignal(complexity=0.2, budget_pressure=0.0, latency_pressure=0.0),
            set(),
        )
        assert decision.model == "deepseek-v4-flash"
        print(f"\n简单任务选: {decision}")

    def test_failed_set_excludes(self):
        # pro 在本轮失败集合里 → 只能选 flash
        decision = choose_model(
            MODEL_PROFILES,
            RoutingSignal(complexity=0.85, budget_pressure=0.0, latency_pressure=0.0, requires_long_context=True),
            {"deepseek-v4-pro"},
        )
        assert decision.model == "deepseek-v4-flash"
        print(f"\nfailed 排除 pro 后选: {decision}")

    def test_exhausted_raises(self):
        with pytest.raises(RuntimeError, match="no eligible"):
            choose_model(
                MODEL_PROFILES,
                RoutingSignal(complexity=0.5, budget_pressure=0.0, latency_pressure=0.0),
                {"deepseek-v4-pro", "deepseek-v4-flash"},
            )

    def test_budget_pressure_penalizes_cost(self):
        # 高预算压力 → 贵的 pro 被惩罚，选便宜的 flash
        decision = choose_model(
            MODEL_PROFILES,
            RoutingSignal(complexity=0.6, budget_pressure=1.0, latency_pressure=0.0),
            set(),
        )
        assert decision.model == "deepseek-v4-flash"
        print(f"\n高预算压力选: {decision}")

    def test_deterministic(self):
        signal = RoutingSignal(complexity=0.6, budget_pressure=0.3, latency_pressure=0.3)
        assert choose_model(MODEL_PROFILES, signal, set()) == choose_model(MODEL_PROFILES, signal, set())
        print("\n确定性：相同输入 → 相同输出")


class TestExtractComplexity:
    def test_search_is_low(self):
        sig = extract_complexity(Intent.SEARCH, 100)
        assert sig.complexity < 0.3
        assert sig.confidence > 0.8
        print(f"\nSEARCH 复杂度: {sig}")

    def test_refactor_is_high(self):
        sig = extract_complexity(Intent.REFACTOR, 5000, changed_files=5)
        assert sig.complexity > 0.8
        print(f"\nREFACTOR 复杂度: {sig}")

    def test_length_adds_complexity(self):
        short = extract_complexity(Intent.CODE, 50)
        long = extract_complexity(Intent.CODE, 5000)
        assert long.complexity > short.complexity
        print(f"\n长度加成: short={short.complexity:.2f}, long={long.complexity:.2f}")


class TestDecisionAudit:
    def test_record(self):
        audit = DecisionAudit()
        audit.record("routing", "model=pro", "complexity=0.85")
        audit.record("verification", "mode=full", "changed=5 files")
        assert len(audit.to_dict()) == 2
        print(f"\n审计: {audit.to_dict()}")
