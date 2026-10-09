"""layered_context：分层上下文预算（Day 18）。

把系统指令、任务目标、工作记忆、近期交互分成四层，各自分配 token 预算，超了按
priority 淘汰。这是「怎样把内容装进请求」的机制——working_memory 解决「保护什么」，
layered_context 解决「怎样装进请求」（课件 Day 18）。

分层（参考项目）：
- SYSTEM：系统指令、全局规则；
- PROJECT：项目记忆、约定、架构；
- SESSION：工作记忆 + 近期交互；
- SCRATCHPAD：草稿、中间结果。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from minicode.context_manager import estimate_tokens


class ContextLayer(str, Enum):
    SYSTEM = "system"
    PROJECT = "project"
    SESSION = "session"
    SCRATCHPAD = "scratchpad"


@dataclass
class LayerContent:
    """一层里的一条内容。"""

    text: str = ""
    tokens: int = 0
    priority: int = 0
    timestamp: float = field(default_factory=time.time)
    source: str = ""


@dataclass
class ContextBudget:
    """分层预算：总上限 + 各层比例。"""

    total_limit: int = 8000
    system_ratio: float = 0.15
    project_ratio: float = 0.25
    session_ratio: float = 0.45
    scratchpad_ratio: float = 0.15

    def limit_for(self, layer: ContextLayer) -> int:
        ratio = {
            ContextLayer.SYSTEM: self.system_ratio,
            ContextLayer.PROJECT: self.project_ratio,
            ContextLayer.SESSION: self.session_ratio,
            ContextLayer.SCRATCHPAD: self.scratchpad_ratio,
        }[layer]
        return int(self.total_limit * ratio)


class LayeredContext:
    """按层存放内容，各自预算，超了按 priority 淘汰。"""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()
        self._layers: dict[ContextLayer, list[LayerContent]] = {
            layer: [] for layer in ContextLayer
        }
        self._tokens: dict[ContextLayer, int] = {layer: 0 for layer in ContextLayer}

    def add(
        self,
        layer: ContextLayer,
        text: str,
        *,
        priority: int = 0,
        source: str = "",
    ) -> None:
        """向某层添加内容，超预算自动淘汰低 priority 的旧内容。"""
        tokens = estimate_tokens(text)
        content = LayerContent(text=text, tokens=tokens, priority=priority, source=source)
        self._layers[layer].append(content)
        self._tokens[layer] += tokens
        self._trim(layer)

    def get_text(self, layer: ContextLayer) -> str:
        """取某一层的完整文本。"""
        return "\n\n".join(c.text for c in self._layers[layer] if c.text)

    def get_all_text(self) -> str:
        """组装所有层，按 SYSTEM → PROJECT → SESSION → SCRATCHPAD 顺序。"""
        parts: list[str] = []
        for layer in ContextLayer:
            text = self.get_text(layer)
            if text:
                parts.append(f"<!-- {layer.value} -->\n{text}")
        return "\n\n".join(parts)

    def layer_tokens(self, layer: ContextLayer) -> int:
        return self._tokens[layer]

    def _trim(self, layer: ContextLayer) -> None:
        """超预算时按「priority 高优先保留、同 priority 越旧越先淘汰」裁剪。"""
        limit = self.budget.limit_for(layer)
        contents = self._layers[layer]
        if self._tokens[layer] <= limit:
            return
        ordered = sorted(contents, key=lambda c: (-c.priority, c.timestamp))
        kept: list[LayerContent] = []
        total = 0
        for c in ordered:
            if total + c.tokens <= limit:
                kept.append(c)
                total += c.tokens
        kept_ids = {id(c) for c in kept}
        self._layers[layer] = [c for c in contents if id(c) in kept_ids]
        self._tokens[layer] = total
