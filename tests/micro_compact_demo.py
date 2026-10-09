"""Day 17 micro_compact 演示：确定性轻量裁剪（去重复、去空）。

运行（项目根目录）：
    python tests/micro_compact_demo.py
"""

import sys
from pathlib import Path

# 把项目根加入 sys.path（命令行直接运行时会找不到 minicode 包）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.micro_compact import micro_compact


def main():
    messages = [
        {"role": "user", "content": "修复 bug"},
        {"role": "assistant_progress", "content": ""},              # 空 progress → 去空
        {"role": "assistant_progress", "content": "在读文件"},       # 保留
        {"role": "assistant_progress", "content": "在读文件"},       # 连续重复 → 去重
        {"role": "tool_result", "content": "文件内容"},
        {"role": "assistant", "content": ""},                       # 空 assistant → 去空
        {"role": "assistant", "content": "已修复"},
    ]
    compacted = micro_compact(messages)
    print(f"原始 {len(messages)} 条 → 裁剪后 {len(compacted)} 条\n")
    for msg in compacted:
        print(f"  {msg['role']}: {msg.get('content', '')!r}")


if __name__ == "__main__":
    main()
