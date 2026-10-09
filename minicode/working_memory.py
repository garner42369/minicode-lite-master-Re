"""working_memory：受保护工作记忆（Day 17 先定义受保护接口）。

长任务压缩上下文时，有些「必须保留的事实」（任务目标、关键约束）不能被丢掉。
WorkingMemory 就是它们的「保护区」：这些事实供 Stable Task Pack 注入，
按重要度和 TTL 淘汰，不能无限增长。

Day 17 只定义受保护接口；按重要度的完整淘汰策略 Day 18 完善。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkingMemoryEntry:
    """一条受保护工作记忆。"""

    key: str
    content: str
    importance: int = 1        # 重要度，越高越难淘汰
    ttl: float = float("inf")  # 存活秒数，inf = 永不过期
    created_at: float = 0.0


class WorkingMemory:
    """受保护工作记忆：保存「必须保留的事实」，按重要度 + TTL 淘汰。"""

    def __init__(self) -> None:
        self._entries: dict[str, WorkingMemoryEntry] = {}

    def set(
        self,
        key: str,
        content: str,
        *,
        importance: int = 1,
        ttl: float = float("inf"),
    ) -> None:
        """写入一条受保护事实。"""
        self._entries[key] = WorkingMemoryEntry(
            key=key, content=content, importance=importance, ttl=ttl,
            created_at=time.monotonic(),
        )

    def get(self, key: str, default: str | None = None) -> str | None:
        entry = self._entries.get(key)
        return entry.content if entry else default

    def protected_facts(self) -> list[str]:
        """返回全部受保护事实（供 Stable Task Pack 注入），按重要度降序。"""
        entries = sorted(self._entries.values(), key=lambda e: -e.importance)
        return [e.content for e in entries]

    def evict_expired(self) -> list[str]:
        """淘汰 TTL 过期的条目，返回被淘汰的 key。"""
        now = time.monotonic()
        expired = [
            key for key, entry in self._entries.items()
            if now - entry.created_at >= entry.ttl
        ]
        for key in expired:
            del self._entries[key]
        return expired

    def _is_alive(self, entry: WorkingMemoryEntry) -> bool:
        """TTL 是否还未过期（inf 表示永不过期）。"""
        if entry.ttl == float("inf"):
            return True
        return time.monotonic() - entry.created_at < entry.ttl

    def render(self, limit: int = 5) -> str:
        """按重要度降序返回前 limit 条存活事实，供读取/展示受保护记忆。

        注意：受保护记忆进入上下文的正确方式是通过 upsert_stable_task_message
        注入独立 system 消息（见 turn_kernel），而不是塞进压缩摘要——两条线
        分开（详见 docs/memory-context-mixing.md）。render 只用于读取/展示。
        """
        alive = [e for e in self._entries.values() if self._is_alive(e)]
        ordered = sorted(alive, key=lambda e: -e.importance)
        return "\n".join(f"- {e.content}" for e in ordered[:limit])

    def evict_by_budget(self, max_entries: int) -> list[str]:
        """超预算时按「重要度从低到高、同重要度越旧越先」淘汰，关键约束优先保留。

        这是「工作记忆不能无限增长」的兜底——即使没有任何条目 TTL 过期，条目数
        也不能超过 max_entries。淘汰顺序保证：高 importance（用户关键约束）永远
        最后被淘汰。
        """
        if len(self._entries) <= max_entries:
            return []
        ordered = sorted(self._entries.values(), key=lambda e: (e.importance, e.created_at))
        overflow = len(self._entries) - max_entries
        evicted = [e.key for e in ordered[:overflow]]
        for key in evicted:
            del self._entries[key]
        return evicted
