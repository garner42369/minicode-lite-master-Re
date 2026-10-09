"""Day 15 Session 持久化演示：创建/保存/载入 + 损坏/旧 schema 诊断。

运行（项目根目录）：
    python tests/session_demo.py
"""

import json
import sys
import tempfile
from pathlib import Path

# 把项目根加入 sys.path（命令行直接运行时会找不到 minicode 包）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.session import (
    SessionData,
    cleanup_old_sessions,
    format_session_inspect,
    list_sessions,
    load_session,
    save_session,
)


def main():
    with tempfile.TemporaryDirectory() as directory:
        d = Path(directory)

        # 1. 创建 + 保存 + 载入往返
        print("== 创建/保存/载入往返 ==")
        session = SessionData.create(".")
        session.messages.append({"role": "user", "content": "hello"})
        path = save_session(session, d)
        loaded = load_session(path)
        print(f"  session_id: {loaded.session_id[:8]}...")
        print(f"  workspace:  {loaded.workspace}")
        print(f"  messages:   {loaded.messages}")
        print(f"  往返一致:   {loaded.messages == session.messages}")

        # 2. 损坏文件诊断
        print("\n== 损坏文件诊断 ==")
        bad = d / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        try:
            load_session(bad)
        except ValueError as exc:
            print(f"  {exc}")

        # 3. 旧 schema 拒绝
        print("\n== 旧 schema 拒绝 ==")
        old = d / "old.json"
        data = {"session_id": "x", "workspace": ".", "created_at": 0, "updated_at": 0, "schema_version": 0}
        old.write_text(json.dumps(data), encoding="utf-8")
        try:
            load_session(old)
        except ValueError as exc:
            print(f"  {exc}")

        # 4. 管理操作：list / inspect / cleanup
        print("\n== list / inspect / cleanup ==")
        for i in range(3):
            s = SessionData.create(".")
            s.updated_at = 1000 + i  # 手动错开时间戳，模拟新旧
            s.messages.append({"role": "user", "content": f"msg {i}"})
            s.events.append({"event": f"e{i}"})
            save_session(s, d)

        summaries = list_sessions(d)
        print(f"  list: {len(summaries)} 个 session")
        for sm in summaries:
            print(f"    - {sm.session_id[:8]} msgs={sm.message_count} updated={sm.updated_at}")

        latest = load_session(d / f"{summaries[0].session_id}.json")
        print("\n  inspect 最新 session:")
        print(format_session_inspect(latest))

        removed = cleanup_old_sessions(d, keep=1)
        print(f"\n  cleanup(keep=1) 删除: {[r[:8] for r in removed]}")
        print(f"  清理后剩 {len(list_sessions(d))} 个")


if __name__ == "__main__":
    main()
