"""diff_viewer 工具：展示两段文本的 unified diff（只读）。

Day 13 引入。给 Agent 提供只读的 diff 视图，帮助它理解「将要做的改动」。
复用 Day 12 的 file_review.unified_diff，不依赖 git，是纯 diff 展示。
"""

from __future__ import annotations

from minicode.file_review import unified_diff
from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)


def _validate(raw):
    path = raw.get("path", "file")
    before = raw.get("before", "")
    after = raw.get("after", "")
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if not isinstance(before, str) or not isinstance(after, str):
        raise ValueError("before/after must be strings")
    return {"path": path, "before": before, "after": after}


def _run(data, context: ToolContext) -> ToolResult:
    diff = unified_diff(data["path"], data["before"], data["after"])
    return ToolResult(ok=True, output=diff or "(no difference)")


DIFF_VIEWER = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="diff_viewer",
        description="展示两段文本的 unified diff（只读）",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "before": {"type": "string"},
                "after": {"type": "string"},
            },
        },
        capabilities={ToolCapability.READ_ONLY},
    ),
)
