"""list_files 工具：查看某一层目录的条目。"""

from __future__ import annotations

from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)
from minicode.workspace import resolve_workspace_path


def _validate(raw):
    path = raw.get("path", ".")
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    return {"path": path}


def _run(data, context: ToolContext) -> ToolResult:
    target = resolve_workspace_path(context, data["path"])
    if not target.is_dir():
        return ToolResult(ok=False, output=f"not a directory: {data['path']}", error_code="not_directory")
    rows = [f"{'dir ' if p.is_dir() else 'file'} {p.name}" for p in sorted(target.iterdir())]
    return ToolResult(ok=True, output="\n".join(rows[:200]) or "(empty)")


LIST_FILES = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="list_files",
        description="查看某一层目录的条目（文件/子目录）",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        capabilities={ToolCapability.READ_ONLY, ToolCapability.CONCURRENCY_SAFE},
    ),
)
