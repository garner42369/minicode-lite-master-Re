"""Day 28：前馈 + 预测控制器的契约测试。"""

from minicode.feedforward_controller import preconfigure
from minicode.intent_parser import Intent
from minicode.predictive_controller import ExponentialSmoother, predict_trend


class TestFeedforward:
    def test_search_low_budget(self):
        cfg = preconfigure(Intent.SEARCH)
        assert cfg.token_budget == 30_000
        assert cfg.max_concurrent_tools == 6  # 只读可高并发
        print(f"\nSEARCH: {cfg}")

    def test_refactor_high_budget(self):
        cfg = preconfigure(Intent.REFACTOR)
        assert cfg.token_budget == 80_000
        assert cfg.max_concurrent_tools == 2  # 写操作串行
        print(f"\nREFACTOR: {cfg}")


class TestPredictive:
    def test_exponential_smoother(self):
        s = ExponentialSmoother(alpha=0.5)
        assert s.update(1.0) == 1.0
        assert s.update(3.0) == 2.0  # 0.5*3 + 0.5*1 = 2.0
        print(f"\n指数平滑: {s.predict()}")

    def test_predict_trend(self):
        up = [0.1 * i for i in range(1, 21)]  # 0.1..2.0 升序
        assert predict_trend(up) == "up"
        assert predict_trend(list(reversed(up))) == "down"
        assert predict_trend([0.5] * 20) == "stable"
        print("\n趋势预测 up/down/stable")
