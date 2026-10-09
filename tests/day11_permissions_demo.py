"""Day 11 权限系统集成演示：从「模型提议动作」到「权限决策」的完整链路。

运行（项目根目录）：
    python tests/day11_permissions_demo.py

串起 auto_mode（命令风险分类）、permissions（决策）、logging（脱敏日志）三个
模块，展示一个 agent 提议的动作如何被分类、构造请求、审批、记录日志。
（workspace 的路径边界判断属于「工具执行层」，另有 workspace_demo.py 演示。）
"""

import sys
from pathlib import Path

# 把项目根加入 sys.path（命令行直接运行时会找不到 minicode 包）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.auto_mode import classify_command
from minicode.logging_config import setup_logging
from minicode.permissions import (
    Mode,
    PermissionDecision,
    PermissionKind,
    PermissionManager,
    PermissionRequest,
    RiskLevel,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main():
    setup_logging(level="DEBUG", log_to_file=False)
    cwd = str(PROJECT_ROOT)

    # 三个动作：读文件、写文件、危险命令（命令先经 auto_mode 分类）
    read_req = PermissionRequest(
        PermissionKind.PATH, str(PROJECT_ROOT / "README.md"), "读文件", RiskLevel.SAFE
    )
    write_req = PermissionRequest(
        PermissionKind.PATH, str(PROJECT_ROOT / "minicode" / "config.py"), "写文件", RiskLevel.MEDIUM
    )
    level, reason = classify_command("rm", ["-rf", "/tmp/x"])
    cmd_req = PermissionRequest(PermissionKind.COMMAND, "rm -rf /tmp/x", f"命令：{reason}", level)
    print(f"[命令分类] rm -rf /tmp/x -> risk={level.value}（{reason}）\n")

    # 四种模式下对同一批动作的裁决对比
    for mode in (Mode.PLAN, Mode.AUTO, Mode.DEFAULT, Mode.BYPASS):
        print(f"--- {mode.value} 模式 ---")
        manager = PermissionManager(cwd, mode, prompt=_prompt)
        for label, request in (("读文件", read_req), ("写文件", write_req), ("危险命令", cmd_req)):
            decision = manager.check(request)
            feedback = f"（{decision.feedback}）" if decision.feedback else ""
            print(f"  {label:6} -> {decision.verdict}{feedback}")
        print()


def _prompt(request):
    # 模拟交互式询问：默认拒绝（演示里不真正等人输入）
    print(f"    [询问用户] {request.kind.value}:{request.scope} (risk={request.risk.value})")
    return PermissionDecision("deny_once")


if __name__ == "__main__":
    main()
