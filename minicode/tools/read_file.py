"""read_file 工具：按 offset/limit 分页读取 UTF-8 文本文件。"""

from __future__ import annotations

from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)
from minicode.workspace import resolve_workspace_path

_DEFAULT_LIMIT = 8_000
_MAX_LIMIT = 20_000


def _validate(raw):
    path = raw.get("path")
    if not isinstance(path, str):
        raise ValueError("path is required and must be a string")
    return {
        "path": path,
        "offset": int(raw.get("offset", 0)),
        "limit": min(int(raw.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT),
    }


def _run(data, context: ToolContext) -> ToolResult:
    target = resolve_workspace_path(context, data["path"])
    if not target.is_file():
        return ToolResult(ok=False, output=f"file not found: {data['path']}", error_code="not_found")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(ok=False, output="binary or non-UTF-8 file", error_code="binary_file")

    start = data["offset"]
    end = min(len(content), start + data["limit"])
    # 头部标注读取进度，让模型知道「读到哪、还有没有更多」
    header = f"OFFSET={start} END={end} TOTAL={len(content)} TRUNCATED={end < len(content)}\n"
    return ToolResult(ok=True, output=header + content[start:end])


READ_FILE = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="read_file",
        description="读取文本文件内容（读文件请用本工具，勿用 run_command）。按 offset/limit 分页读取 UTF-8 文本文件；offset/limit 单位为字符，读到 TRUNCATED=True 时用 offset=END 继续读下一段",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "offset": {"type": "integer", "description": "字符偏移（不是行号），从 0 开始，默认 0"},
                "limit": {"type": "integer", "description": "读取的字符数，默认 8000，最大 20000"},
            },
        },
        capabilities={ToolCapability.READ_ONLY, ToolCapability.CONCURRENCY_SAFE},
        max_result_size_chars=40_000,
    ),
)
