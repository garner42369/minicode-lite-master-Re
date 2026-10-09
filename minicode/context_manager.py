"""minicode 的上下文预算管理：Token 观测与阈值事件（Day 17 第 1 步）。

长任务最先爆掉的通常不是推理，而是重复内容撑爆上下文。本模块建立 Token
观测，让系统知道「当前用了多少、还剩多少」。

关键诚实性（课件红线）：Provider usage 是「已完成请求的事实」，但下一次请求
的 token 只能估算——所以 ContextStats 标记 source="estimated"，UI 显示
estimated，不拿字符数公式冒充精确计费。
"""

from __future__ import annotations

from dataclasses import dataclass

# 模型上下文窗口表（可更新配置）；价格/窗口会变，未知模型用保守默认值
CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
}
DEFAULT_CONTEXT_WINDOW = 128_000

# 使用率阈值：达到时触发一次事件（用 fired 集合去重，不刷屏）
_USAGE_THRESHOLDS = (0.5, 0.75, 0.9)


@dataclass(frozen=True, slots=True)
class ContextStats:
    """上下文使用统计。source 标记来源：estimated（估算）/ provider（事实）。"""

    estimated_tokens: int
    context_window: int
    reserved_output: int
    source: str = "estimated"

    @property
    def usable_window(self) -> int:
        """可用窗口 = 上下文窗口 - 为输出预留的余量。"""
        return max(1, self.context_window - self.reserved_output)

    @property
    def usage_ratio(self) -> float:
        """使用率 = 估算 token / 可用窗口。"""
        return self.estimated_tokens / self.usable_window


def estimate_tokens(text: str) -> int:
    """粗估 token 数：ASCII 约 4 字符/token，CJK 约 1 字/token。

    这是估算（estimation），不是精确计费——UI 要标注 estimated。
    """
    ascii_chars = sum(ord(char) < 128 for char in text)
    cjk_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + cjk_chars)


def context_window_for(model: str) -> int:
    """查模型上下文窗口；未知模型用保守默认值。"""
    return CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


def threshold_events(stats: ContextStats, fired: set[float]) -> list[str]:
    """计算新触发的阈值事件。已触发的阈值不重复（去重，不刷屏）。

    fired 是调用方维护的「已触发阈值」集合，本函数会原地更新它。
    """
    events: list[str] = []
    for threshold in _USAGE_THRESHOLDS:
        if stats.usage_ratio >= threshold and threshold not in fired:
            fired.add(threshold)
            events.append(
                f"context usage {threshold:.0%} "
                f"({stats.estimated_tokens}/{stats.usable_window} tokens, {stats.source})"
            )
    return events
