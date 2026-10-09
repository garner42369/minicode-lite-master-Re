"""grep_files 工具：按正则搜索文本内容（大小写不敏感）。"""

from __future__ import annotations

import re

from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)
from minicode.workspace import resolve_workspace_path

_MAX_MATCHES = 200
# 跳过这些目录，避免 grep 到缓存/依赖/版本库里的无关内容
_SKIP_PARTS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".idea"}


def _validate(raw):
    pattern = raw.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern is required and must be a non-empty string")
    path = raw.get("path", ".")
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    return {"pattern": pattern, "path": path}


def _run(data, context: ToolContext) -> ToolResult:
    root = resolve_workspace_path(context, data["path"])
    regex = re.compile(data["pattern"], re.IGNORECASE)
    rows: list[str] = []
    # 支持单文件：path 指向文件时直接搜它，目录才递归（否则文件 .rglob 返回空）
    if root.is_file():
        candidates = [root]
        base = root.parent
    else:
        candidates = root.rglob("*")
        base = root
    for path in candidates:
        if not path.is_file():
            continue
        # 跳过隐藏目录与缓存/依赖目录
        if any(part.startswith(".") or part in _SKIP_PARTS for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue  # 二进制或不可读文件跳过
        rel = path.relative_to(base)
        # 匹配行带前后 2 行上下文，`>>` 标出匹配行。避免模型拿行号当字符 offset
        # 反复 read_file 去定位——grep 直接给上下文，读完就能看懂匹配处。
        emitted: set[int] = set()
        match_count = 0
        for number, line in enumerate(lines, 1):
            if not regex.search(line):
                continue
            match_count += 1
            if match_count > _MAX_MATCHES:
                break
            for i in range(max(1, number - 2), min(len(lines), number + 2) + 1):
                if i in emitted:
                    continue
                emitted.add(i)
                mark = ">>" if i == number else "  "
                rows.append(f"{rel}:{i}: {mark}{lines[i - 1]}")
        if match_count > _MAX_MATCHES:
            break
    return ToolResult(ok=True, output="\n".join(rows) or "(no matches)")


GREP_FILES = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="grep_files",
        description="按正则（大小写不敏感）搜索文件内容，返回匹配行及其前后 2 行上下文（>> 标记匹配行）；path 可为单个文件或目录",
        input_schema={
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
        },
        capabilities={ToolCapability.READ_ONLY, ToolCapability.CONCURRENCY_SAFE},
        max_result_size_chars=20_000,
    ),
)
