"""run_command 工具：通用命令执行器（argv 模式，结构化结果）。

Day 13 引入。把「模型提议执行命令」变成「结构化运行证据」。

核心设计：
- argv 模式（不经 shell）：命令以参数数组传递，避免 shell 注入；
- 结构化结果：exit_code/stdout/stderr/duration/timed_out/truncated，
  Runtime 不再从一段字符串猜退出码；
- 非零退出码保持失败（ok 只看 exit_code 和 timed_out，不看 stdout 是否非空）；
- 大输出 head/tail 截断，不塞满模型上下文。

shell 模式（管道/重定向/条件）在第 2 步加入，并升级权限审查。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from minicode.auto_mode import classify_command
from minicode.permissions import PermissionKind, PermissionRequest, RiskLevel
from minicode.tooling import (
    ToolCapability,
    ToolContext,
    ToolDefinition,
    ToolMetadata,
    ToolResult,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """一次命令执行的结构化结果。frozen：结果不可被中途篡改。"""

    argv: tuple[str, ...]
    exit_code: int | None   # 超时被 kill 时为 None（没有正常退出码）
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """成功 = 退出码 0 且未超时。不依赖 stdout 内容——非零退出码即使
        有看似正常的输出也视为失败。"""
        return self.exit_code == 0 and not self.timed_out


def clip(text: str, limit: int) -> tuple[str, bool]:
    """head/tail 截断：超过 limit 时保留首尾各一半，中间用省略标记。

    返回 (截断后的文本, 是否截断)。保留 head 和 tail 是因为命令输出的
    开头（标题/命令名）和结尾（错误/摘要）通常最有信息量。
    """
    if len(text) <= limit:
        return text, False
    half = limit // 2
    omitted = len(text) - limit
    return text[:half] + f"\n...[{omitted} chars omitted]...\n" + text[-half:], True


def run_command(
    argv: list[str],
    cwd: str,
    *,
    timeout: float = 30.0,
    output_limit: int = 20_000,
    encoding: str = "utf-8",
) -> CommandResult:
    """执行命令（argv 模式），返回结构化结果。

    argv：可执行文件 + 参数列表，如 ["python", "-c", "print(1)"]；
    cwd：工作目录；
    timeout：超时秒数，超时被捕获成 timed_out=True；
    output_limit：stdout/stderr 各自的截断上限（字符）；
    encoding：解码子进程输出的编码（Windows 上可能是 gbk/cp936）。
    """
    if not argv:
        raise ValueError("argv cannot be empty")

    started = perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=Path(cwd),
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        stdout, cut_out = clip(completed.stdout.decode(encoding, errors="replace"), output_limit)
        stderr, cut_err = clip(completed.stderr.decode(encoding, errors="replace"), output_limit)
        return CommandResult(
            argv=tuple(argv),
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=(perf_counter() - started) * 1000,
            truncated=cut_out or cut_err,
        )
    except subprocess.TimeoutExpired as exc:
        # 超时：exit_code 为 None，保留已产生的部分输出
        partial = exc.stdout or b""
        if isinstance(partial, str):
            stdout = partial
        else:
            stdout = partial.decode(encoding, errors="replace")
        return CommandResult(
            argv=tuple(argv),
            exit_code=None,
            stdout=clip(stdout, output_limit)[0],
            stderr="command timed out",
            duration_ms=(perf_counter() - started) * 1000,
            timed_out=True,
        )


def run_shell_command(
    command: str,
    cwd: str,
    *,
    timeout: float = 30.0,
    output_limit: int = 20_000,
    encoding: str = "utf-8",
) -> CommandResult:
    """shell 模式执行：command 是完整命令字符串，经 shell 执行。

    支持管道/重定向/条件执行，但有 shell 注入风险——调用方必须先做权限
    审批再调用。Windows 上 shell 是 cmd.exe，语法与 POSIX shell 不同。
    """
    if not command.strip():
        raise ValueError("command cannot be empty")

    started = perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=Path(cwd),
            shell=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        stdout, cut_out = clip(completed.stdout.decode(encoding, errors="replace"), output_limit)
        stderr, cut_err = clip(completed.stderr.decode(encoding, errors="replace"), output_limit)
        return CommandResult(
            argv=(command,),
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=(perf_counter() - started) * 1000,
            truncated=cut_out or cut_err,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        stdout = partial.decode(encoding, errors="replace") if isinstance(partial, bytes) else partial
        return CommandResult(
            argv=(command,),
            exit_code=None,
            stdout=clip(stdout, output_limit)[0],
            stderr="command timed out",
            duration_ms=(perf_counter() - started) * 1000,
            timed_out=True,
        )


def _escalate_shell_risk(risk: RiskLevel) -> RiskLevel:
    """shell 模式风险升级：至少升到 HIGH（shell 注入面更大）。"""
    if risk in {RiskLevel.SAFE, RiskLevel.LOW, RiskLevel.MEDIUM}:
        return RiskLevel.HIGH
    return risk


def _approve_command(context: ToolContext, command_str: str, risk: RiskLevel) -> bool:
    """COMMAND 授权线审批。无权限层时：只允许安全/低风险（只读）命令。"""
    permissions = context.permissions
    if permissions is None:
        # 无交互 headless：未显式授权时不启动有副作用命令
        return risk in {RiskLevel.SAFE, RiskLevel.LOW}
    request = PermissionRequest(
        kind=PermissionKind.COMMAND,
        scope=command_str,
        summary=command_str,
        risk=risk,
    )
    decision = permissions.check(request)
    return decision.verdict in {"allow_once", "allow_turn", "allow_always"}


def _validate(raw):
    argv = raw.get("argv")
    command = raw.get("command")
    if argv is not None and command is not None:
        raise ValueError("provide either argv or command, not both")
    if argv is None and command is None:
        raise ValueError("argv or command is required")
    if argv is not None:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty list of strings")
    if command is not None and (not isinstance(command, str) or not command.strip()):
        raise ValueError("command must be a non-empty string")
    return {"argv": argv, "command": command}


def _run(data, context: ToolContext) -> ToolResult:
    # 1. 确定命令表示 + 风险（argv 走分类，shell 走分类后升级）
    if data["argv"] is not None:
        command_str = " ".join(data["argv"])
        risk, _ = classify_command(data["argv"][0], data["argv"][1:])
    else:
        command_str = data["command"]
        parts = command_str.split()
        risk, _ = classify_command(parts[0], parts[1:]) if parts else (RiskLevel.MEDIUM, "shell")
        risk = _escalate_shell_risk(risk)

    # 2. 审批（COMMAND 授权线）
    if not _approve_command(context, command_str, risk):
        return ToolResult(ok=False, output=f"command denied (risk={risk.value})", error_code="command_denied")

    # 3. 执行
    if data["argv"] is not None:
        result = run_command(data["argv"], context.cwd)
    else:
        result = run_shell_command(data["command"], context.cwd)

    # 4. 结构化结果 → ToolResult（exit_code 等进 metadata，不靠字符串猜）
    output = result.stdout
    if result.stderr and result.stderr != "command timed out":
        output = (output + "\n" + result.stderr).strip()
    return ToolResult(
        ok=result.ok,
        output=output,
        error_code=None if result.ok else ("timed_out" if result.timed_out else "nonzero_exit"),
        metadata={
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "truncated": result.truncated,
        },
    )


RUN_COMMAND = ToolDefinition(
    validator=_validate,
    run=_run,
    metadata=ToolMetadata(
        name="run_command",
        description="执行命令（默认 argv 模式；管道/重定向需显式用 command 走 shell 模式）",
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "command": {"type": "string"},
            },
        },
        capabilities={ToolCapability.DESTRUCTIVE, ToolCapability.REQUIRES_PERMISSION},
    ),
)
