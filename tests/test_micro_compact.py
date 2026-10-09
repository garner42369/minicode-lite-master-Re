"""Day 17：micro_compact 的契约测试。"""

from minicode.micro_compact import micro_compact


class TestMicroCompact:
    def test_removes_consecutive_duplicates(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "hi"},  # 连续重复
            {"role": "user", "content": "hi"},  # 连续重复
        ]
        result = micro_compact(messages)
        print(f"去重后条数={len(result)}")
        assert len(result) == 1

    def test_removes_empty_assistant_content(self):
        messages = [
            {"role": "assistant_progress", "content": ""},   # 空 progress
            {"role": "assistant", "content": ""},            # 空 assistant
            {"role": "assistant", "content": "已修复"},
        ]
        result = micro_compact(messages)
        print(f"去空后: {[m.get('content') for m in result]}")
        assert len(result) == 1
        assert result[0]["content"] == "已修复"

    def test_keeps_non_empty_messages(self):
        messages = [
            {"role": "user", "content": "修复"},
            {"role": "tool_result", "content": "结果"},
        ]
        result = micro_compact(messages)
        print(f"保留条数={len(result)}")
        assert len(result) == 2
