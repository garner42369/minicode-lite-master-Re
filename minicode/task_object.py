"""task_object：一轮任务的稳定目标、约束与预期输出。

Day 14 引入。长上下文压缩后，最近对话可能被摘要；TaskObject 是 Stable Task
Pack 的一部分，把「正在做什么、受什么约束、预期产出什么」固定下来，避免
Agent 在压缩后忘记目标。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from minicode.intent_parser import Intent, classify_intent_with_model, parse_intent


@dataclass(frozen=True, slots=True)
class TaskObject:
    """一轮任务的稳定目标与约束。frozen：任务对象一旦确定不可中途篡改。"""

    goal: str                              # 任务目标（用户原始诉求）
    intent: Intent                          # 意图分类
    constraints: tuple[str, ...] = ()       # 约束（如「不要改测试文件」）
    expected_output: str = ""               # 预期输出描述


def build_task_object(text: str, intent: Intent | None = None, model=None) -> TaskObject:
    """从用户输入构建任务对象。

    意图优先级：显式传入 > LLM 分类（传了 model）> 关键词 fallback（无 model）。
    """
    if intent is not None:
        resolved_intent = intent
    elif model is not None:
        resolved_intent = classify_intent_with_model(model, text)
    else:
        resolved_intent = parse_intent(text)
    return TaskObject(goal=text, intent=resolved_intent)
