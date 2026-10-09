"""Day 27：进度控制器（停滞判断）的契约测试。"""

from minicode.progress_controller import ProgressSignal, detect_stall


class TestDetectStall:
    def test_repeating_without_evidence_stalls(self):
        sig = ProgressSignal(
            recent_tool_calls=("read_file", "read_file", "read_file"),
            new_evidence_count=0,
            remaining_steps=5,
        )
        d = detect_stall(sig)
        assert d.stalled is True
        print(f"\n重复动作停滞: {d}")

    def test_new_evidence_does_not_stall(self):
        sig = ProgressSignal(
            recent_tool_calls=("read_file", "read_file", "read_file"),
            new_evidence_count=1,
            remaining_steps=5,
        )
        d = detect_stall(sig)
        assert d.stalled is False
        print(f"\n有新证据不停滞: {d}")

    def test_no_budget_not_stalled(self):
        sig = ProgressSignal(
            recent_tool_calls=("read_file", "read_file", "read_file"),
            new_evidence_count=0,
            remaining_steps=0,
        )
        d = detect_stall(sig)
        assert d.stalled is False
        print(f"\n无预算不判停滞: {d}")

    def test_mixed_actions_not_stalled(self):
        sig = ProgressSignal(
            recent_tool_calls=("read_file", "grep_files", "read_file"),
            new_evidence_count=0,
            remaining_steps=5,
        )
        d = detect_stall(sig)
        assert d.stalled is False
        print(f"\n动作混合不判停滞: {d}")
