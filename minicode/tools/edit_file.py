"""edit_file 工具：单处精确文本替换（受控写入）。

Day 12 引入。与 write_file 的区别：不整体重写，而是定位一处旧文本、替换成
新文本。适用于小修改，能保留未读内容、换行风格和用户并发修改。

失败条件（严格）：
- 零匹配：old_string 在文件里不存在 → 报错；
- 多匹配且未声明 replace_all：old_string 出现多次 → 报错，要求更精确或 replace_all。
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
    old = raw.get("old_string")
    new = raw.get("new_string")
    replace_all = raw.get("replace_all", False)
    if not isinstance(path, str):
        raise ValueError("path is required and must be a string")
    if not isinstance(old, str) or not old:
        raise ValueError("old_string is required and must be a non-empty string")
    if not isinstance(new, str):
        raise ValueError("new_string is required and must be a string")
    return {"path": path, "old": old, "new": new, "replace_all": bool(replace_all)}


def _make_transform(old: str, new: str, replace_all: bool):
    """构造单替换 transform：零匹配 / 多匹配（未声明 replace_all）报错。"""

    def transform(text: str) -> str:
        count = text.count(old)
        if replace_all:
            return text.replace(old, new)
        if count == 0:
            raise ValueError("old_string not found (0 matches)")
        if count > 1:
            raise ValueError(
                f"old_string matches {count} times; use a more specific string or replace_all=true"
            )
        return text.replace(old, new, 1)

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

    transform = _make_transform(data["old"], data["new"], data["replace_all"])
    approve = _approve_from_context(context, target)
    try:
        checkpoint = apply_reviewed_change(target, transform, approve, group_id="default")
    except ValueError as exc:
        # 零匹配 / 多匹配：这是编辑语义错误，不是系统错误
        return ToolResult(ok=False, output=str(exc), error_code="edit_match_error")
    except PermissionError as exc:
        return ToolResult(ok=False, output=f"edit denied: {exc}", error_code="edit_denied")
    except RuntimeError as exc:
        return ToolResult(ok=False, output=str(exc), error_code="concurrent_change")

    if checkpoint is None:
        return ToolResult(ok=True, output="no change (content already matches)")

    return ToolResult(
        ok=True,
        output=f"edited {target.name}",
        metadata={
            "path": str(target),
            "before_hash": checkpoint.before_hash,
            "after_hash": checkpoint.after_hash,
            "group_id": checkpoint.group_id,
        },
    )


EDIT_FILE = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="edit_file",
        description="单处精确文本替换（零匹配/多匹配报错，除非 replace_all）",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        capabilities={ToolCapability.DESTRUCTIVE, ToolCapability.REQUIRES_PERMISSION},
    ),
)
