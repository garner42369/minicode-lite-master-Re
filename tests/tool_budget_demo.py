"""Day 17 ToolResultBudget 演示：大结果持久化 + head/tail 摘要。

运行（项目根目录）：
    python tests/tool_budget_demo.py
"""

import sys
import tempfile
from pathlib import Path

# 把项目根加入 sys.path（命令行直接运行时会找不到 minicode 包）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.context_compactor import ToolResultBudgetManager


def main():
    with tempfile.TemporaryDirectory() as directory:
        budget = ToolResultBudgetManager(Path(directory), limit=50)

        # 1. 小输出（未超限）
        small = "short output"
        text, meta = budget.process(small)
        print(f"小输出: {text!r} meta={meta}")

        # 2. 大输出（超限）
        big = "x" * 1000
        text, meta = budget.process(big)
        print(f"\n大输出（超限）:")
        print(f"  摘要 head: {text[:40]!r}")
        print(f"  摘要 tail: {text[-40:]!r}")
        print(f"  meta: {meta}")


if __name__ == "__main__":
    main()
