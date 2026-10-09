"""minicode 的内置工具：组装工具画像。

Day 6 注册三个只读工具（观察能力）；Day 12 加入三个写工具（受控写入）；
Day 13 加入命令执行、测试反馈与只读证据工具。
"""

from __future__ import annotations

from minicode.tooling import ToolDefinition, ToolRegistry
from minicode.tools.diff_viewer import DIFF_VIEWER
from minicode.tools.edit_file import EDIT_FILE
from minicode.tools.git import GIT
from minicode.tools.grep_files import GREP_FILES
from minicode.tools.list_files import LIST_FILES
from minicode.tools.load_skill import LOAD_SKILL
from minicode.tools.patch_file import PATCH_FILE
from minicode.tools.read_file import READ_FILE
from minicode.tools.run_command import RUN_COMMAND
from minicode.tools.task import TASK_TOOL
from minicode.tools.test_runner import TEST_RUNNER
from minicode.tools.write_file import WRITE_FILE

# 只读核心工具画像：观察能力
_CORE_TOOLS: list[ToolDefinition] = [LIST_FILES, READ_FILE, GREP_FILES]
# 写工具（Day 12）：受控写入，需审批
_WRITE_TOOLS: list[ToolDefinition] = [WRITE_FILE, EDIT_FILE, PATCH_FILE]
# 命令/测试/证据工具（Day 13）：执行、验证与只读证据
_COMMAND_TOOLS: list[ToolDefinition] = [RUN_COMMAND, TEST_RUNNER, GIT, DIFF_VIEWER]
# Skill 工具（Day 23）：模型按需加载 SKILL.md 全文
_SKILL_TOOLS: list[ToolDefinition] = [LOAD_SKILL]
# Agent 工具（Day 26）：并发委派子 Agent
_AGENT_TOOLS: list[ToolDefinition] = [TASK_TOOL]


def create_core_tool_registry() -> ToolRegistry:
    """创建只含只读核心工具的注册表。"""
    return ToolRegistry(_CORE_TOOLS)


def create_full_tool_registry() -> ToolRegistry:
    """创建含只读 + 写 + 命令/测试 + skill + agent 工具的完整注册表。"""
    return ToolRegistry(_CORE_TOOLS + _WRITE_TOOLS + _COMMAND_TOOLS + _SKILL_TOOLS + _AGENT_TOOLS)
