"""Day 13 run_command 工具演示：argv/shell 模式 + 权限审批。

运行（项目根目录）：
    python tests/run_command_tool_demo.py

展示：无权限层时只读命令放行、有副作用命令拒绝；auto 模式安全自动过；
shell 模式风险升级。
"""

import sys
from pathlib import Path

# 把项目根加入 sys.path（命令行直接运行时会找不到 minicode 包）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.permissions import Mode, PermissionDecision, PermissionManager
from minicode.tooling import ToolContext
from minicode.tools.run_command import RUN_COMMAND


def run(raw, context):
    validated = RUN_COMMAND.validator(raw)
    return RUN_COMMAND.run(validated, context)


def main():
    cwd = "."

    # 1. 无权限层：只读放行，有副作用拒绝
    print("== 无权限层 ==")
    no_perm = ToolContext(cwd=cwd)
    r1 = run({"argv": ["where", "python"]}, no_perm)
    print(f"  只读 where:         ok={r1.ok} ({r1.error_code})")
    r2 = run({"argv": [sys.executable, "-c", "print('side effect')"]}, no_perm)
    print(f"  有副作用 python:    ok={r2.ok} ({r2.error_code})")

    # 2. auto 模式 + prompt：安全自动过，有副作用询问
    print("\n== auto 模式 + prompt ==")

    def prompt(request):
        print(f"  [询问] {request.kind.value}:{request.scope} (risk={request.risk.value})")
        return PermissionDecision("allow_once")

    permissions = PermissionManager(cwd, Mode.AUTO, prompt=prompt)
    with_perm = ToolContext(cwd=cwd, permissions=permissions)
    r3 = run({"argv": ["where", "python"]}, with_perm)
    print(f"  只读 where:         ok={r3.ok}（自动通过，不询问）")
    r4 = run({"argv": [sys.executable, "-c", "print('side effect')"]}, with_perm)
    print(f"  有副作用 python:    ok={r4.ok}（询问后允许）")

    # 3. shell 模式：风险升级到 HIGH
    print("\n== shell 模式（风险升级）==")
    r5 = run({"command": "echo hello"}, with_perm)
    print(f"  shell echo hello:   ok={r5.ok} 输出={r5.output!r}")


if __name__ == "__main__":
    main()
