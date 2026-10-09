"""test_runner 工具：运行 pytest 并解析验证结果（通过/失败/错误/零测试）。

Day 13 引入。基于 run_command 运行 pytest，把「测试输出」解析成结构化验证
证据，取代模型「我觉得修好了」的主观判断。

核心红线：零测试不算验证成功——没收集到任何测试时，即使 pytest 退出码为 0，
也不能宣称「验证通过」，否则空目录/空测试套件会被误判为成功。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from minicode.auto_mode import classify_command
from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)
from minicode.tools.run_command import _approve_command, run_command


@dataclass(frozen=True, slots=True)
class TestSummary:
    """pytest 摘要的结构化表示。"""

    passed: int
    failed: int
    errors: int
    zero_tests: bool

    @property
    def ok(self) -> bool:
        """验证成功 = 有测试 + 无失败 + 无错误。零测试不算成功。"""
        return not self.zero_tests and self.failed == 0 and self.errors == 0


_PASSED_RE = re.compile(r"(\d+) passed")
_FAILED_RE = re.compile(r"(\d+) failed")
_ERROR_RE = re.compile(r"(\d+) error")


def parse_pytest_summary(stdout: str) -> TestSummary:
    """解析 pytest 输出的摘要行，提取 passed/failed/error 与零测试标记。

    pytest 摘要行示例：
    - "10 passed in 0.5s"                  → 全通过
    - "3 passed, 1 failed in 1.0s"         → 有失败
    - "no tests ran in 0.1s" / "collected 0 items" → 零测试
    """
    if "no tests ran" in stdout or "collected 0 items" in stdout:
        return TestSummary(0, 0, 0, zero_tests=True)

    def count(regex):
        match = regex.search(stdout)
        return int(match.group(1)) if match else 0

    return TestSummary(count(_PASSED_RE), count(_FAILED_RE), count(_ERROR_RE), zero_tests=False)


def _validate(raw):
    path = raw.get("path", ".")
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    return {"path": path}


def _run(data, context: ToolContext) -> ToolResult:
    path = data["path"]
    argv = [sys.executable, "-m", "pytest", "-q", path]

    # 运行测试是执行代码，可能有副作用，走 COMMAND 授权线审批
    risk, _ = classify_command("pytest", [path])
    if not _approve_command(context, " ".join(argv), risk):
        return ToolResult(ok=False, output="test run denied", error_code="command_denied")

    result = run_command(argv, context.cwd)
    summary = parse_pytest_summary(result.stdout)

    output = result.stdout
    if result.stderr:
        output = (output + "\n" + result.stderr).strip()

    # ok 由 TestSummary.ok 决定：零测试/失败/错误都不算验证成功
    error_code = None if summary.ok else ("zero_tests" if summary.zero_tests else "tests_failed")
    return ToolResult(
        ok=summary.ok,
        output=output,
        error_code=error_code,
        metadata={
            "exit_code": result.exit_code,
            "passed": summary.passed,
            "failed": summary.failed,
            "errors": summary.errors,
            "zero_tests": summary.zero_tests,
        },
    )


TEST_RUNNER = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="test_runner",
        description="运行 pytest 并解析验证结果（零测试不算通过）",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        capabilities={ToolCapability.DESTRUCTIVE, ToolCapability.REQUIRES_PERMISSION},
    ),
)
