"""Day 28：成本闭环的契约测试。"""

from minicode.cost_control import CostController


class TestCostController:
    def test_over_spending_tightens(self):
        c = CostController(target_cost_per_min=0.5)
        adj = c.decide(cost_per_min=1.0)  # 消耗速度超目标
        assert adj.budget_multiplier < 1.0
        print(f"\n超支收紧: {adj}")

    def test_under_spending_loosens(self):
        c = CostController(target_cost_per_min=0.5)
        adj = c.decide(cost_per_min=0.1)  # 消耗速度低于目标
        assert adj.budget_multiplier > 1.0
        print(f"\n低消耗放宽: {adj}")

    def test_total_budget_exhausted(self):
        c = CostController(total_budget=1.0)
        c.record_spend(1.0)  # 已花完总预算
        adj = c.decide(cost_per_min=0.5)
        assert adj.budget_multiplier == 0.3  # 强制收紧，不再用 PID
        print(f"\n总预算耗尽: {adj}")
