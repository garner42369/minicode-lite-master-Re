"""Day 13 集成验收：失败 → 编辑 → 通过 的完整闭环。

运行（项目根目录）：
    python tests/day13_integration_demo.py

用 buggy_calculator 的临时副本（不污染 fixture），串起 test_runner（运行测试）
与 edit_file（修复缺陷），展示「运行证据」如何驱动「受控修复」。
"""

import shutil
import sys
import tempfile
from pathlib import Path

# 把项目根加入 sys.path（命令行直接运行时会找不到 minicode 包）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.permissions import Mode, PermissionDecision, PermissionManager
from minicode.tooling import ToolContext
from minicode.tools.edit_file import EDIT_FILE
from minicode.tools.test_runner import TEST_RUNNER

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "buggy_calculator"


def make_context(cwd):
    """auto 模式 + 自动允许的 prompt，模拟交互审批。"""

    def prompt(request):
        print(f"    [审批] {request.kind.value}:{request.scope}")
        return PermissionDecision("allow_once")

    permissions = PermissionManager(str(cwd), Mode.AUTO, prompt=prompt)
    return ToolContext(cwd=str(cwd), permissions=permissions)


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        calc_dir = Path(temp_dir) / "buggy_calculator"
        shutil.copytree(FIXTURE, calc_dir)
        context = make_context(temp_dir)

        # 1. 运行测试（失败）
        print("== 1. 运行测试（应失败）==")
        r1 = TEST_RUNNER.run(TEST_RUNNER.validator({"path": "buggy_calculator/tests"}), context)
        print(f"  ok={r1.ok} passed={r1.metadata['passed']} failed={r1.metadata['failed']} "
              f"zero_tests={r1.metadata['zero_tests']}")

        # 2. edit_file 修复 divide 的 // → /
        print("\n== 2. edit_file 修复 // → / ==")
        r2 = EDIT_FILE.run(
            EDIT_FILE.validator({
                "path": "buggy_calculator/calculator.py",
                "old_string": "return a // b",
                "new_string": "return a / b",
            }),
            context,
        )
        print(f"  ok={r2.ok} {r2.output}")

        # 3. 再运行测试（通过）
        print("\n== 3. 再运行测试（应通过）==")
        r3 = TEST_RUNNER.run(TEST_RUNNER.validator({"path": "buggy_calculator/tests"}), context)
        print(f"  ok={r3.ok} passed={r3.metadata['passed']} failed={r3.metadata['failed']} "
              f"zero_tests={r3.metadata['zero_tests']}")


if __name__ == "__main__":
    main()
