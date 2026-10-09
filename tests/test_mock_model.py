"""Day 4：离线模型替身的契约测试。

只测契约与边界，不测语言已保证的东西（如 dataclass 构造）。
"""

import pytest

from minicode.mock_model import MockModelAdapter, ScriptedModel
from minicode.types import AgentStep, ChatMessage, ToolCall


# ---- ScriptedModel ----

def test_scripted_model_returns_steps_in_order():
    steps = [
        AgentStep(type="tool_calls", calls=[ToolCall("1", "list_files", {})]),
        AgentStep(type="assistant", kind="final", content="done"),
    ]
    model = ScriptedModel(steps)
    assert model.next([]).calls[0].name == "list_files"
    assert model.next([]).content == "done"


def test_scripted_model_records_message_history():
    # 核心验收：第二次调用能看到第一次的工具结果（历史回填正确）
    model = ScriptedModel([
        AgentStep(type="tool_calls", calls=[ToolCall("1", "read_file", {"path": "a.py"})]),
        AgentStep(type="assistant", kind="final", content="done"),
    ])
    model.next([{"role": "user", "content": "读文件"}])
    tool_result: ChatMessage = {"role": "tool_result", "tool_call_id": "1", "content": "文件内容"}
    model.next([{"role": "user", "content": "读文件"}, tool_result])
    # 第二次调用记录的历史里，包含第一次的工具结果
    assert model.received_messages[1][1]["tool_call_id"] == "1"
    assert model.received_messages[1][1]["content"] == "文件内容"


def test_scripted_model_exhaustion_raises():
    # 核心验收：耗尽时明确报错，不悄悄重复最后一步
    model = ScriptedModel([AgentStep(type="assistant", kind="final", content="only")])
    model.next([])
    with pytest.raises(RuntimeError):
        model.next([])


def test_scripted_model_streams_content():
    chunks: list[str] = []
    model = ScriptedModel([AgentStep(type="assistant", kind="final", content="hello")])
    model.next([], on_stream_chunk=chunks.append)
    assert chunks == ["hello"]


# ---- MockModelAdapter ----

def test_mock_adapter_parses_read_command():
    adapter = MockModelAdapter()
    step = adapter.next([{"role": "user", "content": "/read README.md"}])
    assert step.type == "tool_calls"
    assert step.calls[0].name == "read_file"
    assert step.calls[0].arguments["path"] == "README.md"


def test_mock_adapter_summarizes_tool_result():
    adapter = MockModelAdapter()
    messages: list[ChatMessage] = [
        {"role": "user", "content": "/read README.md"},
        {"role": "tool_result", "tool_call_id": "1", "tool_name": "read_file", "content": "hello"},
    ]
    step = adapter.next(messages)
    assert step.type == "assistant"
    assert step.kind == "final"
    assert "hello" in step.content
