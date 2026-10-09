"""load_skill 工具：让模型按需加载一个本地 SKILL.md 的全文（Day 23）。

发现阶段只暴露摘要，模型判断某 skill 匹配当前任务时才调用本工具读全文——避免把
所有 Skill 全文塞进 prompt。cwd 从 ToolContext 取，不在创建时绑定。
"""

from __future__ import annotations

from minicode.skills import load_skill
from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)


def _validate(raw):
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required and must be a non-empty string")
    return {"name": name.strip()}


def _run(data, context: ToolContext) -> ToolResult:
    skill = load_skill(context.cwd, data["name"])
    if skill is None:
        return ToolResult(
            ok=False,
            output=f"Unknown skill: {data['name']}",
            error_code="not_found",
        )
    return ToolResult(
        ok=True,
        output="\n".join(
            [
                f"SKILL: {skill.name}",
                f"SOURCE: {skill.source}",
                f"PATH: {skill.path}",
                "",
                skill.content,
            ]
        ),
    )


LOAD_SKILL = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="load_skill",
        description="Load a local SKILL.md by name.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        capabilities={ToolCapability.READ_ONLY, ToolCapability.CONCURRENCY_SAFE},
    ),
)
