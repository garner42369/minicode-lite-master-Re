"""Day 13 test_runner 演示：解析 pytest 摘要（通过/失败/零测试）+ 真实运行。

运行（项目根目录）：
    python tests/test_runner_demo.py
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
from minicode.tools.test_runner import TEST_RUNNER, parse_pytest_summary


def main():
    # 1. parse 纯逻辑：三种典型 pytest 输出
    print("== parse_pytest_summary 解析 ==")
    samples = {
        "全通过": "collected 72 items\n72 passed in 0.5s\n",
        "有失败": "collected 5 items\n3 passed, 2 failed in 1.0s\n",
        "零测试": "no tests ran in 0.1s\n",
    }
    for label, output in samples.items():
        s = parse_pytest_summary(output)
        print(f"  {label}: passed={s.passed} failed={s.failed} errors={s.errors} "
              f"zero_tests={s.zero_tests} ok={s.ok}")

    # 2. 真实运行 test_runner（带权限，auto 模式 pytest 会询问）
    print("\n== 真实运行 test_runner ==")

    def prompt(request):
        print(f"  [询问] {request.kind.value}:{request.scope} (risk={request.risk.value})")
        return PermissionDecision("allow_once")

    permissions = PermissionManager(".", Mode.AUTO, prompt=prompt)
    context = ToolContext(cwd=".", permissions=permissions)
    validated = TEST_RUNNER.validator({"path": "tests/test_config.py"})
    result = TEST_RUNNER.run(validated, context)
    print(f"  结果: ok={result.ok} passed={result.metadata['passed']} "
          f"failed={result.metadata['failed']} zero_tests={result.metadata['zero_tests']}")


if __name__ == "__main__":
    main()
