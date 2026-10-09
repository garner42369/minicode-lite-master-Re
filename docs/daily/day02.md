# Day 2：包骨架与统一协议

## 今日任务

建立最小可安装的 Python 包骨架，并定义系统里**唯一的一套消息与动作协议**，让模型、工具、Loop、UI 说同一种语言。

交付物：

- `pyproject.toml` —— 包元数据、入口、依赖、pytest 配置
- `minicode/__init__.py` —— 包标识
- `minicode/types.py` —— 消息、工具调用、模型步骤、运行事件、模型适配器协议
- `minicode/tooling.py` —— 工具的定义、元数据、上下文与统一结果
- `tests/test_protocols.py` —— 协议构造、序列化、ID 配对的离线测试

## 任务分析

**为什么第一天不能只写 `agent_loop.py`？**

如果模型、工具、Loop、UI 各用一套字典结构（比如模型返回 `{"toolName": ...}`、工具返回 `{"name": ...}`），那么每加一个 Provider、每加一个工具，都要改核心循环。今天的核心目标是先冻结「共同语言」，让所有层都引用 `types.py` / `tooling.py` 里的同一批类型。

三条必须满足的约束（来自课件）：

1. **Provider 的原始 JSON 不离开 Adapter** —— Adapter 把外部格式翻译成统一协议；
2. **工具调用必须有稳定 ID** —— 用于把结果配对回对应的调用；
3. **进度与最终回答必须可区分** —— 否则 Loop 会在进度播报后错误结束。

## 代码实现

### 1. pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "minicode"
version = "0.1.0"
description = "本地 AI Coding Agent Runtime"
requires-python = ">=3.11"
dependencies = []                    # 运行时零依赖

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]

[project.scripts]
minicode-py = "minicode.main:main"          # Day 21 实现
minicode-headless = "minicode.headless:main"  # Day 21 实现

[tool.setuptools.packages.find]
include = ["minicode*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--ignore=tests/fixtures"   # 忽略故意失败的 buggy_calculator
```

要点：

- `dependencies = []` 是项目定位的核心表达：**运行时零依赖、自研**。
- `[project.scripts]` 指向的 `minicode.main` / `minicode.headless` 现在还不存在（Day 21 才建），但 setuptools 安装时不校验目标模块，运行时才解析，所以可以先声明。

### 2. minicode/types.py —— 系统共同语言

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, TypedDict


class ChatMessage(TypedDict, total=False):
    """一条对话消息。total=False 表示字段可缺省（不同角色用不同字段子集）。"""
    role: Literal[
        "system", "user", "assistant",
        "assistant_progress", "assistant_tool_call", "tool_result",
    ]
    content: str
    tool_call_id: str   # 工具结果消息用它配对回对应的 tool call
    tool_name: str
    input: dict[str, Any]
    is_error: bool


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型发起的一次工具调用。id 必须稳定，用于与 ToolResult 配对。"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class AgentStep:
    """模型的一步产出：要么是文本（assistant），要么是工具请求（tool_calls）。"""
    type: Literal["assistant", "tool_calls"]
    content: str = ""
    kind: Literal["final", "progress"] | None = None
    # 用 field(default_factory=list) 而非 = []：避免所有实例共享同一个可变列表
    calls: list[ToolCall] = field(default_factory=list)


RuntimeEventCategory = Literal[
    "phase", "compaction", "guard", "widening", "recovery", "stop",
]


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """运行时向 UI 发出的结构化事件，用于可观测性。"""
    category: RuntimeEventCategory  # 事件类别
    message: str                    # 人类可读描述
    step: int | None = None         # 第几步；None 表示与步骤无关
    profile: str = ""               # 运行画像，如 single / single-deep
    phase: str = ""                 # 阶段，如 explore / execute / verify
    stop_reason: str = ""           # 停止原因
    widening_reason: str = ""       # widening 原因
    evidence_summary: str = ""      # 验证证据摘要


class ModelAdapter(Protocol):
    """模型适配器协议。上层 Loop 只依赖 next()，不关心背后是哪个 Provider。"""
    def next(
        self,
        messages: list[ChatMessage],                            # 当前完整对话历史
        on_stream_chunk: Callable[[str], None] | None = None,   # 可选流式回调
        store: Any | None = None,                               # 可选会话存储引用
    ) -> AgentStep: ...
```

### 3. minicode/tooling.py —— 工具协议

```python
class ToolCapability(str, Enum):
    """工具能力标志，供调度与权限判断使用。"""
    READ_ONLY = "read_only"
    DESTRUCTIVE = "destructive"
    CONCURRENCY_SAFE = "concurrency_safe"
    REQUIRES_PERMISSION = "requires_permission"


@dataclass
class ToolMetadata:
    """工具的完整描述：名称、说明、输入 schema、能力、开关、输出上限、标签。"""
    name: str
    description: str
    capabilities: set[ToolCapability] = field(default_factory=set)
    input_schema: dict[str, Any] = field(default_factory=dict)
    is_enabled: bool = True
    max_result_size_chars: int = 10_000
    tags: list[str] = field(default_factory=list)

    @property
    def is_read_only(self) -> bool:
        return ToolCapability.READ_ONLY in self.capabilities
    # is_destructive / is_concurrency_safe 同理


@dataclass(slots=True)
class ToolResult:
    """工具执行后的统一回执。"""
    ok: bool
    output: str
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolContext:
    """工具运行上下文。"""
    cwd: str
    permissions: Any | None = None   # Day 11 引入 PermissionManager
    session: Any | None = None


Validator = Callable[[Any], Any]
Runner = Callable[[Any, ToolContext], ToolResult]


@dataclass(slots=True)
class ToolDefinition:
    """执行逻辑（validator + run）+ 完整描述（metadata）。"""
    validator: Validator
    run: Runner
    metadata: ToolMetadata
```

### 4. tests/test_protocols.py

核心测试：工具调用 ID 配对、AgentStep 的 final/progress 区分、假 Adapter 走通「消息 → 模型 → AgentStep」、工具结果统一回执、`RuntimeEvent` 序列化。

```python
class _HelloAdapter:
    def next(self, messages, on_stream_chunk=None, store=None):
        return AgentStep(type="assistant", content="hello", kind="final")

def test_model_adapter_protocol_roundtrip():
    adapter: ModelAdapter = _HelloAdapter()
    messages: list[ChatMessage] = [{"role": "user", "content": "hi"}]
    step = adapter.next(messages)
    assert step.type == "assistant"
    assert step.content == "hello"
    assert step.kind == "final"
```

## 讲解

### 每个模块为什么存在

| 类型 | 作用 | 谁消费 |
| --- | --- | --- |
| `ChatMessage` | 对话消息的统一表示 | Adapter、Loop、Session |
| `ToolCall` | 模型请求的一次工具调用（带稳定 ID） | Loop、ToolRegistry |
| `AgentStep` | 模型一步产出（文本 / 工具请求） | Loop |
| `RuntimeEvent` | 运行过程的结构化事件 | UI、评估 harness |
| `ModelAdapter` | 模型适配器协议，屏蔽 Provider 差异 | Loop |
| `ToolMetadata` | 工具的完整描述（自包含，可独立传递） | prompt 构建、Registry |
| `ToolResult` | 工具执行的统一回执 | Loop |
| `ToolContext` | 工具运行的上下文 | 每个工具 |
| `ToolDefinition` | 工具的执行逻辑 + 描述 | Registry（Day 5） |

### 数据流

```
用户消息 (ChatMessage)
  → ModelAdapter.next()  → AgentStep
        ├── type="assistant", kind="final"   → 结束
        ├── type="assistant", kind="progress" → 继续播报
        └── type="tool_calls"                → 拆出 ToolCall，交给 Registry 执行
                                                  → ToolResult 回填为 tool_result 消息
```

### 关键设计决策：ToolDefinition vs ToolMetadata 怎么分

最终采用「**删 definition 的重复、让 metadata 自包含**」：

- `ToolMetadata` 持有 `name` / `description` / `input_schema` + 运行属性（`capabilities` / `is_enabled` / `max_result_size_chars` / `tags`），**可以独立传递**——例如构建 prompt 时只需要工具描述，不需要执行函数。
- `ToolDefinition` 只留 `validator` / `run` / `metadata`，职责是「怎么执行」。

代价：访问工具名要写 `tool.metadata.name` 而不是 `tool.name`；`metadata` 因此是**必填**（否则工具没名字）。

### 与参考源码的差异（不逐行照搬的体现）

1. **字段命名**：参考源码里对接 API 的字段用 camelCase（`toolUseId` / `toolName` / `isError`），纯内部字段用 snake_case（`stop_reason`）。我们统一用 snake_case，把「Provider JSON → 内部协议」的翻译收敛到 Day 10 的 Adapter 内部，比参考源码更干净。
2. **ToolCall**：参考源码用 `TypedDict`，我们用 `frozen dataclass`（不可变、可哈希、有类型提示）。
3. **ToolMetadata 结构**：参考源码里 `ToolDefinition` 与 `ToolMetadata` 都重复了 `name`/`description`/`input_schema`（演进痕迹）；我们去掉了 definition 侧的重复。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| `python -m compileall -q minicode tests` 通过 | ✅ |
| 离线测试不读 `.env`、不访问网络 | ✅ |
| 仓库只有一个 `AgentStep` 定义 | ✅ |
| 协议测试通过 | ✅ `9 passed` |
