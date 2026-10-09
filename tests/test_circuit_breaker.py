"""Day 18：压缩熔断（CompactionCircuitBreaker）的契约测试。"""

from minicode.circuit_breaker import CompactionCircuitBreaker


class TestCompactionCircuitBreaker:
    def test_consecutive_failures_open_breaker(self):
        # 核心契约：连续 3 次失败 → 熔断（防死循环）
        breaker = CompactionCircuitBreaker()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_allowed() is True   # 2 次还没到阈值
        breaker.record_failure()
        assert breaker.is_allowed() is False  # 第 3 次熔断
        print(f"连续 3 次失败 → is_allowed=False（熔断）")

    def test_success_resets_consecutive_failures(self):
        # 核心契约：一次成功清零连续失败计数，不累积
        breaker = CompactionCircuitBreaker()
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()              # 清零
        breaker.record_failure()              # 重新从 1 开始
        assert breaker.is_allowed() is True   # 没到 3，不熔断
        print(f"成功清零连续失败计数 → 后续失败重新计数")

    def test_manual_reset_reopens_breaker(self):
        # 核心契约：手动 reset 重新闭合熔断
        breaker = CompactionCircuitBreaker()
        for _ in range(3):
            breaker.record_failure()
        assert breaker.is_allowed() is False  # 已熔断
        breaker.reset()
        assert breaker.is_allowed() is True   # 手动重置后恢复
        print(f"手动 reset → is_allowed=True（重新闭合）")
