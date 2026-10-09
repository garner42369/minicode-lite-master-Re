"""Day 16 replay 演示：只读回放事件历史。

运行（项目根目录）：
    python tests/replay_demo.py
"""

import sys
from pathlib import Path

# 把项目根加入 sys.path（命令行直接运行时会找不到 minicode 包）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.session import SessionData, format_session_replay


def main():
    session = SessionData.create(".")
    session.events.append({"type": "user", "summary": "修复 divide bug"})
    session.events.append({"type": "tool", "summary": "edit_file calculator.py"})
    session.events.append({"type": "tool", "summary": "test_runner 2 passed"})
    session.events.append({"type": "assistant", "summary": "已修复，测试通过"})

    print("== replay（只读回放，不调模型/工具）==")
    print(format_session_replay(session))

    empty = SessionData.create(".")
    print(f"\n== 空事件 session ==\n{format_session_replay(empty)}")


if __name__ == "__main__":
    main()
