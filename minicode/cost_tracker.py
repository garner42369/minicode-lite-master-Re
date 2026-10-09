"""minicode 的成本记录：token 用量与费用的事实快照。

Day 10 引入。CostTracker 只「记录事实」，不做「预算政策」——
要不要省钱、超了预算怎么办，属于更高层（Day 27 的控制论）的决策。
这里只保证每一笔模型调用的 token 与费用被如实、可累加地记下来，
供后续观测、成本控制和评估报告消费。

设计约束：价格不硬编码在 tracker 里。模型目录里的价格、上下文窗口会
随时间变化（课件「今日易错点」），因此单价由调用方（model_registry 的
ModelInfo）提供，tracker 只负责「乘与加」，不负责「查价」。
"""

from __future__ import annotations

from dataclasses import dataclass


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    input_price_per_mtok: float,
    output_price_per_mtok: float,
) -> float:
    """按「每百万 token 单价」计算一次调用的美元费用。

    token 单价单位是 USD / 1M tokens（行业惯例），所以 token 数要先除以
    1_000_000 再乘单价。输入与输出分开计价，因为模型对两者的定价不同。
    """
    input_cost = input_tokens * input_price_per_mtok / 1_000_000
    output_cost = output_tokens * output_price_per_mtok / 1_000_000
    return input_cost + output_cost


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """一次模型调用的用量与费用快照。frozen 保证记录不可被中途篡改。"""

    model: str        # 模型名，用于按模型分账
    input_tokens: int
    output_tokens: int
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """累计汇总：token 总量、总费用与调用次数。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


class CostTracker:
    """按调用逐笔记录 token/费用，并支持整体或按模型的累计查询。

    用法：每次 Provider 拿到 usage 后调用 record()；需要报告或预算判断时
    再调用 totals() 汇总。记录与汇总分离，避免每次 record 都重算全部历史。
    """

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
    ) -> UsageRecord:
        """记录一笔调用，返回该记录以便调用方就地使用。"""
        record = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        self._records.append(record)
        return record

    def records(self) -> list[UsageRecord]:
        """返回全部记录的副本，避免调用方改动内部列表。"""
        return list(self._records)

    def totals(self, model: str | None = None) -> UsageTotals:
        """累计汇总。传入 model 则只统计该模型；否则统计全部。"""
        total_input = 0
        total_output = 0
        total_cost = 0.0
        calls = 0
        for record in self._records:
            if model is not None and record.model != model:
                continue
            total_input += record.input_tokens
            total_output += record.output_tokens
            total_cost += record.cost_usd
            calls += 1
        return UsageTotals(
            input_tokens=total_input,
            output_tokens=total_output,
            cost_usd=total_cost,
            calls=calls,
        )
