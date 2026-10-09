"""task 工具：并发委派多个子 Agent 处理复杂任务（Day 26）。

子 Agent = 独立的 agent 循环（内部递归调 run_agent_turn），有自己的 messages、过滤后的
工具集、步数上限。一次 task 调用委派 N 个子任务，工具内部用 ThreadPoolExecutor 并发跑，
父 Agent 等这批全返回后汇总继续（方案 1：批内并发）。

关键约束：
- explore / plan 强制只读（工具集只含只读工具），general 全工具；
- 子 Agent 失败（stop_reason != done）导致整个 task 工具返回 ok=False，不被当空成功；
- 委派深度限制（depth 经 ToolContext 显式传递，线程安全），防止无限递归；
- model/tools 从 ToolContext 取（run_agent_turn 已放入），避免「task 工具需要 model、
  model 需要工具清单」的循环依赖，因此 task 工具可以静态注册。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from minicode.agent_loop import run_agent_turn
from minicode.tooling import ToolDefinition, ToolMetadata, ToolRegistry, ToolResult

AGENT_TYPES = {
    "explore": {
        "name": "Explore",
        "system_prompt": (
            "You are an exploration agent. Analyze the requested files using read-only tools. "
            "Focus on their structure, responsibilities, dependencies, and key flows. "
            "Read the target files first. Inspect related files only to resolve a specific "
            "question necessary for the requested report; do not recursively explore dependencies. "
            "read_file offset and limit are character counts, NOT line numbers. "
            "Start at offset=0. For full-file reading, use limit=8000 and continue from END. "
            "Do not use grep line numbers as character offsets. "
            "Once you can explain the requested structure and responsibilities, "
            "stop using tools and provide a concise report. "
            "State uncertainties explicitly instead of continuing open-ended exploration."
        ),
        "allowed_tools": {"list_files", "read_file", "grep_files"},
        "max_turns": 15,
    },
    "plan": {
        "name": "Plan",
        "system_prompt": (
                "You are a planning agent. Use read-only tools to gather enough evidence "
                "to produce an actionable plan for the user's task. "
                "Identify the relevant code, proposed changes, constraints, and validation steps. "
                "Inspect additional files only when they could change the plan. "
                "Do not attempt to understand the entire codebase. "
                "Once you can state what to change, why, and how to verify it, "
                "stop exploring and return the plan. "
                "State assumptions and unresolved questions explicitly; "
                "do not investigate every uncertainty. "
                "read_file offset and limit use characters, NOT line numbers. "
                "For full-file reading, start at offset=0, use limit=8000, "
                "and continue from the previous END."
        ),
        "allowed_tools": {"list_files", "read_file", "grep_files"},
        "max_turns": 15,
    },
    "general": {
        "name": "General",
        "system_prompt": (
            "You are a general-purpose coding agent. You can read, write, and modify code. "
            "Break complex tasks into smaller steps. When done, provide a summary of what you did."
        ),
        "allowed_tools": None,  # 全工具
        "max_turns": 15,
    },
}

_MAX_DEPTH = 2          # 委派深度上限：子 Agent 再委派子 Agent 最多 2 层
_MAX_CONCURRENT = 4     # 批内并发上限（课件「并发/递归上限明确拒绝」）
# 子 Agent 用低成本快速模型（flash）：探索/规划类子任务量大、对深度要求低
_SUBAGENT_MODEL = "deepseek-v4-flash"


def _validate(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("input must be an object")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks is required and must be a non-empty list")
    normalized = []
    for item in tasks:
        if not isinstance(item, dict):
            raise ValueError("each task must be an object")
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("each task needs a description")
        agent_type = item.get("agent_type", "general")
        if agent_type not in AGENT_TYPES:
            raise ValueError(f"agent_type must be one of: {', '.join(AGENT_TYPES)}")
        prompt = item.get("prompt") or description
        normalized.append({
            "description": description.strip(),
            "agent_type": agent_type,
            "prompt": prompt.strip() if isinstance(prompt, str) else description.strip(),
        })
    return {"tasks": normalized}


def _run_subagent(task: dict, model, tools, cwd: str, permissions, depth: int) -> tuple[str, str | None, str | None]:
    """跑一个子 Agent，返回 (description, final, stop)。stop 非 None 表示失败。"""
    agent_def = AGENT_TYPES[task["agent_type"]]
    allowed = agent_def["allowed_tools"]
    if allowed is not None:
        filtered = [t for t in tools.list() if t.metadata.name in allowed]
        sub_tools = ToolRegistry(filtered)
        # 模型也要只看到过滤后的工具（否则 explore/plan 的模型仍能看见 write_file/run_command）
        sub_model = model.with_tools(filtered) if hasattr(model, "with_tools") else model
    else:
        sub_tools = tools
        sub_model = model
    # 子 Agent 换用低成本快速模型（flash）
    if hasattr(sub_model, "with_model"):
        sub_model = sub_model.with_model(_SUBAGENT_MODEL)

    budget_hint = (
        f"\n\nYou have at most {agent_def['max_turns']} turns. "
        "Reserve your last 2-3 turns to write the final report — "
        "once you have enough information, stop exploring and summarize."
    )
    sub_messages = [
        {"role": "system", "content": agent_def["system_prompt"] + f"\n\nCurrent cwd: {cwd}" + budget_hint},
        {"role": "user", "content": task["prompt"]},
    ]
    desc = task["description"]
    print(f"      [子Agent:{desc}] 启动 max_steps={agent_def['max_turns']}")
    history, stop = run_agent_turn(
        sub_model, sub_tools, sub_messages, cwd,
        permissions=permissions,
        max_steps=agent_def["max_turns"],
        depth=depth,
        announce_budget=True,
        on_tool_start=lambda c: print(f"      [子Agent:{desc}] 工具 {c.name}({c.arguments})"),
        on_tool_result=lambda c, r: print(f"      [子Agent:{desc}] 结果 {c.name} ok={r.ok}"),
    )
    print(f"      [子Agent:{desc}] 结束 stop={stop}")
    if stop != "done":
        return task["description"], None, stop
    for msg in reversed(history):
        if msg.get("role") == "assistant" and str(msg.get("content", "")).strip():
            return task["description"], str(msg["content"]), None
    return task["description"], None, "no_final_message"


def _run(data: dict, context) -> ToolResult:
    model = context.model
    tools = context.tools
    if model is None or tools is None:
        return ToolResult(ok=False, output="cannot run sub-agent: no model/tools in context")
    if context.depth >= _MAX_DEPTH:
        return ToolResult(ok=False, output=f"max sub-agent recursion depth ({_MAX_DEPTH}) reached")

    tasks = data["tasks"]
    sub_depth = context.depth + 1
    start = time.time()

    workers = min(len(tasks), _MAX_CONCURRENT)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_subagent, t, model, tools, context.cwd, context.permissions, sub_depth) for t in tasks]
        results = [f.result() for f in futures]

    parts: list[str] = []
    any_failed = False
    for description, final, stop in results:
        if stop is not None:
            any_failed = True
            parts.append(f"[{description}] FAILED (stop={stop})")
        else:
            parts.append(f"[{description}]\n{final}")

    elapsed = time.time() - start
    header = f"[Sub-agents completed] {len(tasks)} task(s), %.1fs\n\n" % elapsed
    return ToolResult(ok=not any_failed, output=header + "\n\n".join(parts))


TASK_TOOL = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="task",
        description=(
            "Launch one or more sub-agents to handle complex tasks concurrently. "
            "Each task has an agent_type: 'explore' (fast, read-only), 'plan' (thorough, read-only), "
            "or 'general' (full tools). Results are returned once all sub-agents finish."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "Short 3-5 word description"},
                            "prompt": {"type": "string", "description": "Full task description for the sub-agent"},
                            "agent_type": {"type": "string", "enum": ["explore", "plan", "general"]},
                        },
                        "required": ["description"],
                    },
                }
            },
            "required": ["tasks"],
        },
    ),
)
