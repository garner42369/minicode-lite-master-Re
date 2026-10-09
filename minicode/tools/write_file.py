"""write_file 工具：新建或完整替换文件（受控写入）。

Day 12 引入，是第一个「写」工具。与 edit_file / patch_file 的区别：不基于旧
内容做替换，而是直接提供完整的新内容，适用于新建文件或整体重写。

写入走统一的 review helper（file_review.apply_reviewed_change），审批通过
ToolContext.permissions 桥接到 Day 11 的 PermissionManager 的 EDIT 授权线。
"""

from __future__ import annotations

from minicode.file_review import apply_reviewed_change
from minicode.permissions import PermissionKind, PermissionRequest, RiskLevel
from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)
from minicode.workspace import resolve_workspace_path


def _validate(raw):
    path = raw.get("path")
    content = raw.get("content")
    if not isinstance(path, str):
        raise ValueError("path is required and must be a string")
    if not isinstance(content, str):
        raise ValueError("content is required and must be a string")
    return {"path": path, "content": content}


def _approve_from_context(context: ToolContext, target) -> object:
    """从 ToolContext 构造审批回调：桥接 PermissionManager 的 EDIT 授权线。

    无权限层（context.permissions 为 None）时默认拒绝——安全第一，宁可
    不让写，也不能未经审批就落盘。
    """
    permissions = context.permissions
    if permissions is None:
        return lambda diff: False

    def approve(diff_text: str) -> bool:
        request = PermissionRequest(
            kind=PermissionKind.EDIT,
            scope=str(target),
            summary=diff_text,
            risk=RiskLevel.MEDIUM,
        )
        decision = permissions.check(request)
        return decision.verdict in {"allow_once", "allow_turn", "allow_always"}

    return approve


def _run(data, context: ToolContext) -> ToolResult:
    # 1. 解析到工作区内的规范路径（越界会抛 PermissionError）
    try:
        target = resolve_workspace_path(context, data["path"])
    except PermissionError as exc:
        return ToolResult(ok=False, output=str(exc), error_code="path_escape")

    # 2. 完整替换：transform 忽略 before，直接返回新内容
    def transform(before: str) -> str:
        return data["content"]

    # 3. 审批：桥接权限层；group_id 暂用默认（Day 15 session 接入后从任务 ID 取）
    approve = _approve_from_context(context, target)
    try:
        checkpoint = apply_reviewed_change(target, transform, approve, group_id="default")
    except PermissionError as exc:
        return ToolResult(ok=False, output=f"edit denied: {exc}", error_code="edit_denied")
    except RuntimeError as exc:
        return ToolResult(ok=False, output=str(exc), error_code="concurrent_change")

    if checkpoint is None:
        return ToolResult(ok=True, output="no change (content already matches)")

    return ToolResult(
        ok=True,
        output=f"wrote {target.name} ({len(checkpoint.after_hash)} chars hash)",
        metadata={
            "path": str(target),
            "before_hash": checkpoint.before_hash,
            "after_hash": checkpoint.after_hash,
            "group_id": checkpoint.group_id,
        },
    )


WRITE_FILE = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="write_file",
        description="新建或完整替换文件内容（受控写入，需审批）",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        capabilities={ToolCapability.DESTRUCTIVE, ToolCapability.REQUIRES_PERMISSION},
    ),
)
