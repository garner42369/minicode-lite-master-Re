"""runtime_profiles：不同运行画像的预算、重试与 widen 策略。

Day 14 引入。课件红线：「不要把阈值散落在 Loop」。所有预算/重试/widen 阈值
集中到 profile，而不是写死在 agent_loop 里，便于按任务类型切换策略。

single：标准单一任务，预算紧、失败阈值低；
single-deep：深度/复杂任务，预算大、更积极 widen（深度任务需要更多探索）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """一种运行画像的资源策略。frozen：画像不可变。"""

    name: str
    max_steps: int                   # 步数预算
    max_consecutive_failures: int    # 同工具连续失败阈值（达阈值停止）
    empty_response_retry_limit: int  # 空响应重试上限
    widen_after_stagnation: int      # 停滞多少步后触发 widen（扩大搜索）

    def step_exhausted(self, step: int) -> bool:
        """当前步数是否已耗尽预算。"""
        return step >= self.max_steps


SINGLE = RuntimeProfile(
    name="single",
    max_steps=8,
    max_consecutive_failures=3,
    empty_response_retry_limit=3,
    widen_after_stagnation=3,
)

SINGLE_DEEP = RuntimeProfile(
    name="single-deep",
    max_steps=16,
    max_consecutive_failures=5,
    empty_response_retry_limit=3,
    widen_after_stagnation=2,
)

# SEARCH（只读解释）任务的步数上限：比默认 16 小，防止开放式解释任务反复重读
# 文件耗尽步数（v0.1 发现的「无限探索」）。但 4 太短——模型「读几个文件 + 总结」
# 就需要 4-5 步，还没总结就 max_steps；6 仍不够「探索 + 执行 + 诊断 + 报告」这条
# 更长的链（v0.2 发现），故调到 10。
SEARCH_MAX_STEPS = 10

# 按画像名取 profile
PROFILES = {SINGLE.name: SINGLE, SINGLE_DEEP.name: SINGLE_DEEP}


def get_profile(name: str) -> RuntimeProfile:
    """按名字取画像；未知名字退回 single（保守）。"""
    return PROFILES.get(name, SINGLE)
