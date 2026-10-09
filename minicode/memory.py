"""minicode 的项目记忆：跨会话知识（Day 19）。

三层记忆的定位（课件 Day 19/20 复盘）：
- Context：本次请求的对话；
- Working Memory：当前任务受保护事实（Day 18）；
- Project Memory：跨会话、跨进程的知识（本模块）——「下次还需要知道什么」。

三条红线（课件易错点）：
1. 记忆 ≠ 聊天归档：只记可复用的知识（测试命令、目录约定、已确认的架构决定、
   代码风格）；不记密钥、一次性错误、未验证的猜测、可从源码重读的全文。
2. scope 隔离比检索算法更重要：不同工作区不能因关键词相同互相注入私有事实。
3. 搜索命中 ≠ 内容仍然真实：当前指令和重新读取的事实优先级高于旧记忆。
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from minicode.context_manager import estimate_tokens


class MemoryScope(str, Enum):
    """记忆作用域。隔离是记忆系统的第一原则。"""

    USER = "user"        # 跨项目，~/.mini-code/memory/
    PROJECT = "project"  # 项目共享，<workspace>/.mini-code-memory/
    LOCAL = "local"      # 项目本地，<workspace>/.mini-code-memory-local/


@dataclass
class MemoryEntry:
    """一条项目记忆（事实 / 决策 / 约定 / 风格）。"""

    id: str
    content: str
    scope: MemoryScope
    category: str = "general"           # architecture / convention / decision / pattern...
    tags: list[str] = field(default_factory=list)
    source: str = ""                     # 来源（哪个文件 / 命令 / 决策）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    confidence: float = 1.0              # 置信度，冲突时降低
    valid: bool = True                   # 有效性，失效后不注入
    expires_at: float | None = None      # 过期时间戳，None = 永不过期

    def __post_init__(self) -> None:
        # content 在搜索/评分/格式化里被当 str 用（.lower()/.strip()/[:N]），
        # 构造时强制转 str，避免一条畸形数据让整个记忆搜索崩溃。
        if not isinstance(self.content, str):
            self.content = "" if self.content is None else str(self.content)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "scope": self.scope.value,
            "category": self.category,
            "tags": self.tags,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confidence": self.confidence,
            "valid": self.valid,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        return cls(
            id=data["id"],
            content=data["content"],
            scope=MemoryScope(data.get("scope", "project")),
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            source=data.get("source", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            confidence=data.get("confidence", 1.0),
            valid=data.get("valid", True),
            expires_at=data.get("expires_at"),
        )


@dataclass(frozen=True, slots=True)
class MemoryPaths:
    """三 scope 的存储路径。

    user 是全局的（所有 workspace 共享），project/local 绑定 workspace 路径——
    这天然实现了 scope 隔离：不同 workspace 的 project/local 落在不同目录，不会
    因关键词相同互相注入私有事实。
    """

    user_memory: Path
    project_memory: Path
    local_memory: Path

    @classmethod
    def for_workspace(cls, workspace: str | Path) -> "MemoryPaths":
        workspace_path = Path(workspace).resolve()
        return cls(
            user_memory=Path.home() / ".mini-code" / "memory",
            project_memory=workspace_path / ".mini-code-memory",
            local_memory=workspace_path / ".mini-code-memory-local",
        )

    def for_scope(self, scope: MemoryScope) -> Path:
        return {
            MemoryScope.USER: self.user_memory,
            MemoryScope.PROJECT: self.project_memory,
            MemoryScope.LOCAL: self.local_memory,
        }[scope]


def save_memory_file(path: Path, entries: list[MemoryEntry]) -> None:
    """原子保存记忆到 JSON 文件。

    原子写（mkstemp + os.replace）：保存中断不会留下「半截 JSON」被当成有效记忆。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [e.to_dict() for e in entries]}
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_memory_file(path: Path) -> list[MemoryEntry]:
    """从 JSON 载入记忆。文件不存在返回空列表；损坏抛可诊断的 ValueError。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"corrupt memory file: {path}") from exc
    return [MemoryEntry.from_dict(item) for item in data.get("entries", [])]


def _recover_entries(entries: list[MemoryEntry]) -> tuple[list[MemoryEntry], int]:
    """恢复损坏条目：去无效 ID、去重 ID、修空 category、去空 content。

    返回 (恢复后的列表, 移除数)。空 content 的条目直接移除；空 category 补成
    "general"（可修，不丢内容）。
    """
    seen_ids: set[str] = set()
    recovered: list[MemoryEntry] = []
    removed = 0
    for entry in entries:
        if not entry.id or not isinstance(entry.id, str):
            removed += 1
            continue
        if entry.id in seen_ids:
            removed += 1
            continue
        if not entry.category or not isinstance(entry.category, str):
            entry.category = "general"
        if not entry.content:
            removed += 1
            continue
        seen_ids.add(entry.id)
        recovered.append(entry)
    return recovered, removed


# 敏感信息模式：add/update 时拒绝落盘（课件红线：敏感模式内容不自动落盘）。
_SECRET_PATTERN = re.compile(r"(?i)(api[_ -]?key|password|secret|token)\s*[:=]")

# ── 检索：分词 + 标准 Okapi BM25 ───────────────────────────────────────

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]")

_BM25_K1 = 1.5
_BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    """分词：英文单词 + CJK 单字（中文无空格，单字是基本匹配单位）。"""
    tokens = [w.lower() for w in _WORD_RE.findall(text)]
    tokens.extend(_CJK_RE.findall(text))
    return tokens


def _compute_idf(documents: list[list[str]]) -> dict[str, float]:
    """经典 BM25 IDF：log(1 + (N - df + 0.5) / (df + 0.5))。"""
    n = len(documents)
    if n == 0:
        return {}
    doc_freq: dict[str, int] = {}
    for doc in documents:
        for term in set(doc):
            doc_freq[term] = doc_freq.get(term, 0) + 1
    return {
        term: math.log(1 + (n - df + 0.5) / (df + 0.5))
        for term, df in doc_freq.items()
    }


def _compute_avgdl(documents: list[list[str]]) -> float:
    """平均文档长度（token 数）。"""
    if not documents:
        return 0.0
    return sum(len(doc) for doc in documents) / len(documents)


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avgdl: float,
    *,
    k1: float = _BM25_K1,
    b: float = _BM25_B,
) -> float:
    """标准 Okapi BM25 评分。

    tf 用原始词频（出现次数），长度归一化只由分母的 b * (|d|/avgdl) 承担——
    不在这里除以 doc_len，否则长度影响被算两遍。
    """
    if not query_tokens or not doc_tokens or avgdl == 0:
        return 0.0
    doc_len = len(doc_tokens)
    score = 0.0
    for term in set(query_tokens):
        if term not in idf:
            continue
        tf = doc_tokens.count(term)  # 原始词频，不归一化
        if tf == 0:
            continue
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * (doc_len / avgdl))
        score += idf[term] * (numerator / denominator)
    return score


class MemoryManager:
    """管理三 scope 的记忆：CRUD + 敏感拒绝 + 显式 save（原子落盘）。"""

    def __init__(self, workspace: str | Path | None = None) -> None:
        resolved = workspace if workspace is not None else Path.cwd()
        self.workspace = str(Path(resolved).resolve())
        self.paths = MemoryPaths.for_workspace(self.workspace)
        self._entries: dict[MemoryScope, list[MemoryEntry]] = {
            MemoryScope.USER: [],
            MemoryScope.PROJECT: [],
            MemoryScope.LOCAL: [],
        }
        self._load_all()

    def _load_all(self) -> None:
        for scope in MemoryScope:
            entries = load_memory_file(self._scope_file(scope))
            recovered, removed = _recover_entries(entries)
            if removed:
                # 发现损坏条目，恢复后立即落盘修复，避免每次都重复恢复
                save_memory_file(self._scope_file(scope), recovered)
            self._entries[scope] = recovered

    def _scope_file(self, scope: MemoryScope) -> Path:
        return self.paths.for_scope(scope) / "memory.json"

    @staticmethod
    def _reject_secret(content: str) -> None:
        if _SECRET_PATTERN.search(content):
            raise ValueError("refusing to store possible secret")

    def add(
        self,
        scope: MemoryScope,
        content: str,
        *,
        category: str = "general",
        tags: list[str] | None = None,
        source: str = "",
        confidence: float = 1.0,
    ) -> MemoryEntry:
        """添加一条记忆。含敏感信息则抛 ValueError 拒绝落盘。"""
        self._reject_secret(content)
        entry = MemoryEntry(
            id=uuid.uuid4().hex,
            content=content,
            scope=scope,
            category=category,
            tags=list(tags or []),
            source=source,
            confidence=confidence,
        )
        self._entries[scope].append(entry)
        return entry

    def update(self, scope: MemoryScope, entry_id: str, content: str) -> bool:
        """按 id 更新记忆内容，返回是否找到。含敏感信息则抛 ValueError。"""
        self._reject_secret(content)
        for entry in self._entries[scope]:
            if entry.id == entry_id:
                entry.content = content
                entry.updated_at = time.time()
                return True
        return False

    def delete(self, scope: MemoryScope, entry_id: str) -> bool:
        """按 id 删除记忆，返回是否删除成功。"""
        before = len(self._entries[scope])
        self._entries[scope] = [e for e in self._entries[scope] if e.id != entry_id]
        return len(self._entries[scope]) < before

    def list(self, scope: MemoryScope) -> list[MemoryEntry]:
        """列出某 scope 的全部记忆（返回副本，不改内部状态）。"""
        return list(self._entries[scope])

    def add_tag(self, scope: MemoryScope, entry_id: str, tag: str) -> bool:
        for entry in self._entries[scope]:
            if entry.id == entry_id and tag not in entry.tags:
                entry.tags.append(tag)
                entry.updated_at = time.time()
                return True
        return False

    def remove_tag(self, scope: MemoryScope, entry_id: str, tag: str) -> bool:
        for entry in self._entries[scope]:
            if entry.id == entry_id and tag in entry.tags:
                entry.tags.remove(tag)
                entry.updated_at = time.time()
                return True
        return False

    def save(self, scope: MemoryScope | None = None) -> None:
        """原子落盘。scope=None 时保存全部三 scope。

        除 memory.json（内部存储）外，同时写 MEMORY.md（格式化版本）——模型/人读
        MEMORY.md 得到干净的 markdown，而不是 memory.json 的原始内部字段（id/时间戳/
        confidence 这些是噪声不是知识）。
        """
        scopes = [scope] if scope is not None else list(MemoryScope)
        for s in scopes:
            save_memory_file(self._scope_file(s), self._entries[s])
            self._save_markdown(s)

    def _save_markdown(self, scope: MemoryScope) -> None:
        """把某 scope 的记忆格式化成 MEMORY.md 落盘（派生文件，可从 memory.json 再生成）。"""
        markdown = self._format_scope_markdown(scope, self._entries[scope])
        md_path = self.paths.for_scope(scope) / "MEMORY.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(markdown, encoding="utf-8")

    def search(
        self,
        query: str,
        scope: MemoryScope | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """BM25 检索。scope=None 时跨全部 scope，按相关性降序、去重、取前 limit 条。"""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scopes = [scope] if scope is not None else list(MemoryScope)
        scored: list[tuple[float, MemoryEntry]] = []
        query_lower = query.lower()
        for s in scopes:
            entries = [e for e in self._entries[s] if e.valid]
            if not entries:
                continue
            entry_tokens = [
                _tokenize(f"{e.content} {e.category} {' '.join(e.tags)}")
                for e in entries
            ]
            idf = _compute_idf(entry_tokens)
            avgdl = _compute_avgdl(entry_tokens)
            for i, e in enumerate(entries):
                bm25 = _bm25_score(query_tokens, entry_tokens[i], idf, avgdl)
                # confidence 修正 + 子串加分（query 完整命中 content 加权）
                score = bm25 * e.confidence
                if query_lower in e.content.lower():
                    score += 2.0
                if score > 0:
                    scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        seen: set[str] = set()
        result: list[MemoryEntry] = []
        for _, e in scored:
            key = e.content[:100].strip().lower()
            if key not in seen:
                seen.add(key)
                result.append(e)
        return result[:limit]

    def get_relevant_context(
        self,
        query: str | None = None,
        max_entries: int = 20,
        max_tokens: int = 8000,
    ) -> str:
        """记忆注入入口：检索（或全部）+ 预算限制 + markdown 格式化。

        有 query → BM25 检索相关记忆；无 query → 全部记忆。优先级 LOCAL > PROJECT
        > USER（越局部越优先），逐条加入直到 token 超预算。返回要注入 system prompt
        的文本；删除某条记忆后，这里自然不再返回它（课件验收「删除后不再注入」）。
        """
        q = (query or "").strip()
        parts: list[str] = []
        total_tokens = 0
        for scope in (MemoryScope.LOCAL, MemoryScope.PROJECT, MemoryScope.USER):
            entries = self.search(q, scope=scope, limit=max_entries) if q else self.list(scope)
            if not entries:
                continue
            accepted: list[MemoryEntry] = []
            for e in entries[:max_entries]:
                candidate = self._format_scope_markdown(scope, [*accepted, e])
                if total_tokens + estimate_tokens(candidate) <= max_tokens:
                    accepted.append(e)
                else:
                    break
            if not accepted:
                continue
            formatted = self._format_scope_markdown(scope, accepted)
            parts.append(formatted)
            total_tokens += estimate_tokens(formatted)
        return "\n\n".join(parts)

    def _format_scope_markdown(self, scope: MemoryScope, entries: list[MemoryEntry]) -> str:
        """把一个 scope 的记忆格式化成 MEMORY.md 风格的 markdown（按 category 分组）。"""
        scope_names = {
            MemoryScope.USER: "User Memory",
            MemoryScope.PROJECT: "Project Memory",
            MemoryScope.LOCAL: "Local Memory",
        }
        lines = [f"# {scope_names[scope]}", ""]
        categories: dict[str, list[MemoryEntry]] = {}
        for e in entries:
            categories.setdefault(e.category, []).append(e)
        for category, cat_entries in categories.items():
            lines.append(f"## {category.title()}")
            lines.append("")
            for e in cat_entries:
                tags_str = f" `{' '.join(e.tags)}`" if e.tags else ""
                lines.append(f"- {e.content}{tags_str}")
            lines.append("")
        return "\n".join(lines)
