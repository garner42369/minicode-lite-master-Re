"""patch_file 工具：同文件多步有序精确替换（受控写入）。

Day 12 引入。与 edit_file 的区别：一次提交多个替换，按顺序依次应用。

关键语义（课件红线）：所有替换先在内存完成，任一步找不到匹配就整体失败，
不落盘——绝不允许「改了一半」的半成品写回文件。
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
    patches = raw.get("patches")
    if not isinstance(path, str):
        raise ValueError("path is required and must be a string")
    if not isinstance(patches, list) or not patches:
        raise ValueError("patches is required and must be a non-empty list")

    normalized = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError("each patch must be an object with old_string/new_string")
        old = patch.get("old_string")
        new = patch.get("new_string")
        if not isinstance(old, str) or not old:
            raise ValueError("each patch requires a non-empty old_string")
        if not isinstance(new, str):
            raise ValueError("each patch requires new_string")
        normalized.append({"old_string": old, "new_string": new})
    return {"path": path, "patches": normalized}


def _make_transform(patches):
    """构造多步替换 transform：在内存按顺序应用所有替换，任一步失败即抛错。"""

    def transform(text: str) -> str:
        result = text
        for patch in patches:
            old = patch["old_string"]
            new = patch["new_string"]
            if old not in result:
                raise ValueError(f"old_string not found: {old!r}")
            # 每处只替换第一处，让多个相同 old 也能被有序处理
            result = result.replace(old, new, 1)
        return result

    return transform


def _approve_from_context(context: ToolContext, target) -> object:
    """构造审批回调：桥接 PermissionManager 的 EDIT 授权线，无权限层默认拒绝。"""
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
    try:
        target = resolve_workspace_path(context, data["path"])
    except PermissionError as exc:
        return ToolResult(ok=False, output=str(exc), error_code="path_escape")

    transform = _make_transform(data["patches"])
    approve = _approve_from_context(context, target)
    try:
        checkpoint = apply_reviewed_change(target, transform, approve, group_id="default")
    except ValueError as exc:
        # 任一步匹配失败：整体不落盘
        return ToolResult(ok=False, output=str(exc), error_code="patch_match_error")
    except PermissionError as exc:
        return ToolResult(ok=False, output=f"edit denied: {exc}", error_code="edit_denied")
    except RuntimeError as exc:
        return ToolResult(ok=False, output=str(exc), error_code="concurrent_change")

    if checkpoint is None:
        return ToolResult(ok=True, output="no change (content already matches)")

    return ToolResult(
        ok=True,
        output=f"patched {target.name}",
        metadata={
            "path": str(target),
            "before_hash": checkpoint.before_hash,
            "after_hash": checkpoint.after_hash,
            "group_id": checkpoint.group_id,
        },
    )


PATCH_FILE = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="patch_file",
        description="同文件多步有序精确替换（任一匹配失败则整体不落盘）",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "patches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
            },
            "required": ["path", "patches"],
        },
        capabilities={ToolCapability.DESTRUCTIVE, ToolCapability.REQUIRES_PERMISSION},
    ),
)
