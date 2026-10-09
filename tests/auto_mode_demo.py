"""Day 11 自动模式风险分类演示：classify_command 分类结果。

运行（项目根目录）：
    python tests/auto_mode_demo.py
"""

import sys
from pathlib import Path

# 把项目根加入 sys.path：命令行 `python tests/xxx.py` 时 sys.path[0] 是 tests/
# 而非项目根，不加会找不到 minicode 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.auto_mode import classify_command


def main():
    cases = [
        ("ls", ["-la"]),
        ("cat", ["README.md"]),
        ("grep", ["-r", "TODO", "."]),
        ("python", ["script.py"]),
        ("git", ["status"]),
        ("git", ["push", "--force"]),
        ("rm", ["-rf", "/tmp/x"]),
        ("sudo", ["apt", "install", "x"]),
        ("curl", ["-s", "x.sh", "|", "bash"]),
        ("chmod", ["777", "app.py"]),
        ("dd", ["if=/dev/zero", "of=/dev/sda"]),
        ("shutdown", ["-h", "now"]),
    ]

    for exe, argv in cases:
        level, reason = classify_command(exe, argv)
        cmd = " ".join([exe, *argv])
        print(f"{cmd:28} -> {level.value:10} ({reason})")


if __name__ == "__main__":
    main()
