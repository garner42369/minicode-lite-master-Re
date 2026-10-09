"""Day 30：真实 Provider 修复 buggy_calculator 的端到端验证。

复制 fixture 到临时目录（全新 workspace），真实模型自己跑测试、定位并修复失败原因，
然后 pytest 验证 2 passed。任务描述刻意「模糊」——不告诉模型 bug 在哪、怎么改，让它
自己定位（这才是「真实修复」，不是「照指令改」）。

用法：

    python tests/v04_fix_buggy_harness.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.config import load_dotenv
from minicode.permissions import Mode, PermissionDecision, RiskLevel
from minicode.runtime_factory import RuntimeFactory
from minicode.task_object import build_task_object

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "buggy_calculator"

# 刻意模糊：只告诉模型「有测试失败」，不透露 bug 位置和改法
_TASK = (
    "这个项目有一个失败的测试。请运行测试、查看失败原因，定位并修复代码里的缺陷，"
    "然后重新运行测试确认全部通过。"
)


def run_fix(cwd: str):
    def prompt(request):
        if request.risk is RiskLevel.DANGEROUS:
            print(f"    [审批] {request.kind.value}:{request.scope} -> deny")
            return PermissionDecision("deny_once")
        print(f"    [审批] {request.kind.value}:{request.scope} -> allow")
        return PermissionDecision("allow_once")

    factory = RuntimeFactory(mode=Mode.AUTO, prompt=prompt)
    runtime = factory.create(cwd)
    task = build_task_object(_TASK, model=runtime.adapter)

    history, stop = runtime.run(
        _TASK,
        task=task,
        on_tool_start=lambda c: print(f"    [工具] {c.name}({c.arguments})"),
        on_tool_result=lambda c, r: print(f"    [工具结果] {c.name} ok={r.ok}"),
        on_nudge=lambda n: print(f"    [Kernel拦截] {n}"),
    )
    return stop, runtime.cost_tracker.totals()


def _pytest(workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_calculator.py", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",      # pytest 输出含 UTF-8 中文，不能靠系统默认 GBK 解码
        errors="replace",
    )


def main() -> int:
    load_dotenv(str(Path(__file__).resolve().parents[1] / ".env"))

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "buggy_calculator"
        shutil.copytree(FIXTURE, workspace)

        print("=== 修复前：pytest 应失败 ===")
        before = _pytest(workspace)
        print(before.stdout)
        print(f"修复前 pytest 退出码: {before.returncode}")

        print("\n=== 真实模型修复 ===")
        stop, totals = run_fix(str(workspace))
        print(f"  stop_reason: {stop}")

        print("\n=== 修复后：pytest 应通过 ===")
        after = _pytest(workspace)
        print(after.stdout)
        print(f"修复后 pytest 退出码: {after.returncode}")
        print(f"\n成本: 调用 {totals.calls} 次, 输入 {totals.input_tokens}, 输出 {totals.output_tokens}, ${totals.cost_usd:.6f}")

        if after.returncode == 0:
            print("\n=== PASS：真实模型修复成功 ===")
            return 0
        print("\n=== FAIL：修复后 pytest 仍失败 ===")
        print(after.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
