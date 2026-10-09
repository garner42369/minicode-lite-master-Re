"""Day 16 rewind 演示：冲突检测 + 原子回退。

运行（项目根目录）：
    python tests/rewind_demo.py
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

from minicode.file_review import Checkpoint, build_rewind_record, digest, preview_rewind, rewind_group


def main():
    with tempfile.TemporaryDirectory() as directory:
        d = Path(directory)

        # 1. 正常回退（恢复 before）
        print("== 正常回退 ==")
        f1 = d / "a.txt"
        f1.write_bytes(b"after content")
        cp1 = Checkpoint(str(f1), True, b"before content", digest(b"before content"), digest(b"after content"), "g1")
        print(f"  preview: {preview_rewind([cp1])}")
        rewind_group([cp1])
        print(f"  回退后: {f1.read_bytes()!r}")

        # 2. 新建文件回退（删除）
        print("\n== 新建文件回退（删除）==")
        f2 = d / "new.txt"
        f2.write_bytes(b"new file")
        cp2 = Checkpoint(str(f2), False, b"", digest(b""), digest(b"new file"), "g1")
        print(f"  preview: {preview_rewind([cp2])}")
        rewind_group([cp2])
        print(f"  回退后文件存在: {f2.exists()}")

        # 3. 冲突检测（用户后来改过 → 报告冲突，不覆盖）
        print("\n== 冲突检测 ==")
        f3 = d / "conflict.txt"
        f3.write_bytes(b"after")
        cp3 = Checkpoint(str(f3), True, b"before", digest(b"before"), digest(b"after"), "g1")
        f3.write_bytes(b"user edited later")  # 模拟用户后来改过
        try:
            rewind_group([cp3])
        except RuntimeError as exc:
            print(f"  {exc}")
        print(f"  文件保持用户修改: {f3.read_bytes()!r}")

        # 4. 恢复记录（审计）
        print("\n== 恢复记录（审计）==")
        record = build_rewind_record([cp1, cp2])
        print(f"  timestamp: {record.timestamp:.2f}")
        for action, path in record.actions:
            print(f"    {action} {path}")


if __name__ == "__main__":
    main()
