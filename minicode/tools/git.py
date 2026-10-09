"""git 工具：只读 git 查询（status / diff / log / show）。

Day 13 引入。给 Agent 提供只读工作区证据，帮助它理解仓库状态、改动和
历史。只提供只读子命令；commit / push 等有副作用的操作不在本工具内，
提交行为必须单独走审批。
"""

from __future__ import annotations

from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)
from minicode.tools.run_command import run_command

# 只读子命令白名单：其余子命令（commit/push/checkout/reset...）一律拒绝
_READ_ONLY_SUBCOMMANDS = {"status", "diff", "log", "show", "rev-parse", "branch"}


def _validate(raw):
    subcommand = raw.get("subcommand")
    args = raw.get("args", [])
    if not isinstance(subcommand, str) or not subcommand:
        raise ValueError("subcommand is required and must be a string")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    return {"subcommand": subcommand, "args": args}


def _run(data, context: ToolContext) -> ToolResult:
    subcommand = data["subcommand"]
    if subcommand not in _READ_ONLY_SUBCOMMANDS:
        return ToolResult(
            ok=False,
            output=f"subcommand not allowed: {subcommand}（只读白名单：{sorted(_READ_ONLY_SUBCOMMANDS)}）",
            error_code="not_allowed",
        )
    argv = ["git", subcommand, *data["args"]]
    result = run_command(argv, context.cwd)
    return ToolResult(
        ok=result.ok,
        output=result.stdout or result.stderr,
        metadata={"exit_code": result.exit_code},
    )


GIT = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="git",
        description="只读 git 查询（status/diff/log/show，不含 commit/push）",
        input_schema={
            "type": "object",
            "properties": {
                "subcommand": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["subcommand"],
        },
        capabilities={ToolCapability.READ_ONLY},
    ),
)
