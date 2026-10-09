"""minicode 的非交互入口（Day 21：单次执行 + 稳定退出码 + allow-edits + trace）。

面向 CI / 脚本。与 main.py 共享同一个 RuntimeFactory 装配。机器调用者必须能依靠
退出码判断任务结果——不能把「Error: ...」当成功文本返回 0（课件易错点）。

退出码：
    0 = 成功（stop_reason == done）
    1 = 运行时错误（任务未完成 / 异常）
    2 = 配置错误（如 base_url 缺失）
    3 = 未授权（保留：权限层拒绝副作用导致任务无法继续）
"""

from __future__ import annotations

import argparse
import json
import sys

from minicode.permissions import Mode, PermissionDecision, PermissionRequest, RiskLevel
from minicode.runtime_factory import RuntimeFactory

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_UNAUTHORIZED = 3


def _make_auto_approve_prompt(allow_edits: bool):
    """构造 Headless 的审批 prompt：默认拒绝副作用，--allow-edits 时允许本轮。

    allow_edits 只对当前运行生效，用 allow_once（一次性），绝不持久化为「永远允许」。
    """

    def prompt(request: PermissionRequest) -> PermissionDecision:
        if request.risk is RiskLevel.DANGEROUS:
            return PermissionDecision("deny_once")
        if request.risk is RiskLevel.SAFE:
            return PermissionDecision("allow_once")
        # 中/高风险（写、命令）：默认拒绝，显式 allow_edits 才放行这一次
        return PermissionDecision("allow_once") if allow_edits else PermissionDecision("deny_once")

    return prompt


def _emit_json(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False))


def _emit_text(final: str) -> None:
    print(final)


def run_headless(
    prompt_text: str,
    cwd: str,
    *,
    allow_edits: bool = False,
    output_json: bool = False,
    trace: bool = False,
) -> int:
    """单次执行任务，返回稳定退出码。结果到 stdout，trace 到 stderr。"""
    factory = RuntimeFactory(mode=Mode.AUTO, prompt=_make_auto_approve_prompt(allow_edits))
    runtime = None
    try:
        runtime = factory.create(cwd)
    except ValueError as exc:
        # 配置错误：尽早用清晰错误暴露（如 base_url 缺失），不等到请求发出
        if output_json:
            _emit_json({"exit_code": EXIT_CONFIG_ERROR, "error": str(exc)})
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    def _trace_tool(call) -> None:
        if trace:
            print(f"[tool] {call.name}({call.arguments})", file=sys.stderr)

    try:
        history, stop = runtime.run(
            prompt_text,
            on_tool_start=_trace_tool,
        )
        final = history[-1].get("content", "") if history else ""
        if output_json:
            _emit_json({"exit_code": EXIT_OK if stop == "done" else EXIT_RUNTIME_ERROR, "stop_reason": stop, "output": final})
        else:
            _emit_text(final)
        return EXIT_OK if stop == "done" else EXIT_RUNTIME_ERROR
    except Exception as exc:
        if output_json:
            _emit_json({"exit_code": EXIT_RUNTIME_ERROR, "error": str(exc)})
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    finally:
        if runtime is not None:
            runtime.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="minicode-headless", description="非交互执行入口")
    parser.add_argument("prompt", nargs="?", help="任务指令")
    parser.add_argument("--cwd", default=".", help="工作目录")
    parser.add_argument("--allow-edits", action="store_true", help="显式允许本次运行的有副作用编辑")
    parser.add_argument("--json", action="store_true", dest="output_json", help="以 JSON 输出结构化结果")
    parser.add_argument("--trace", action="store_true", help="把工具调用 trace 输出到 stderr")
    args = parser.parse_args(argv)

    if not args.prompt:
        parser.print_help()
        return EXIT_CONFIG_ERROR

    return run_headless(
        args.prompt,
        args.cwd,
        allow_edits=args.allow_edits,
        output_json=args.output_json,
        trace=args.trace,
    )


if __name__ == "__main__":
    raise SystemExit(main())
