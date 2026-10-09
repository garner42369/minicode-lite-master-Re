"""Day 29：消融（baseline vs cybernetic）的契约测试。"""

from minicode.ablation import AblationProfile, run_ablation


class TestAblation:
    def test_run_ablation(self):
        profiles = [
            AblationProfile(task_id="mild", turns=20, growth_per_turn=0.03),
            AblationProfile(task_id="stress", turns=20, growth_per_turn=0.06),
        ]
        report = run_ablation(profiles)
        assert len(report.results) == 4  # 2 profiles × 2 arms
        assert "delta_cybernetic_minus_baseline" in report.delta
        print(f"\n消融汇总:\n  baseline: {report.delta['baseline']}\n  cybernetic: {report.delta['cybernetic']}\n  delta: {report.delta['delta_cybernetic_minus_baseline']}")

    def test_deterministic(self):
        profiles = [AblationProfile(task_id="x", turns=20, growth_per_turn=0.05)]
        assert run_ablation(profiles).delta == run_ablation(profiles).delta
        print("\n消融确定性")
