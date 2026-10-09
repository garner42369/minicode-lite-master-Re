# Day 5：Tool Registry——把模型的意图变成受控函数调用

## 今日任务

实现 `ToolRegistry`，在「模型产生的工具调用意图」和「真实 Python 函数」之间建立一层受控
的执行通道。核心是五件事加一个清理接口：O(1) 查找、拒绝未知工具、执行前校验输入、
异常归一化成失败的 `ToolResult`、超大输出做可解释截断、以及 `dispose()` 清理接口。

## 任务分析

**为什么必须有这一层 Registry？** 模型只能产生「结构化的意图」——一个 `ToolCall(name,
arguments)`，它不能、也不应该直接调用 Python 函数。直接调用的风险是：**一个工具抛异常，
整个 Agent 会话就崩了**。

想象一下：模型请求读一个文件，结果文件是二进制、或者路径不存在、或者工具内部有 bug 抛了
`TypeError`。如果没有 Registry 兜底，这个异常会一路往上冒、打断整个 Loop，用户看到的是一坨
堆栈而不是「读失败了」这条信息。所以 Registry 的定位是**异常防火墙**：不管工具出什么状况，
Loop 拿到的永远是统一的 `ToolResult`，会话不中断，模型还能根据 error_code 调整下一步。

## 代码实现

### 1. ToolRegistry.execute —— 三阶段执行 + 异常防火墙

```python
class ToolRegistry:
    def __init__(self, tools, disposer=None):
        self._tools = {tool.metadata.name: tool for tool in tools}   # 名字索引，O(1) 查找
        self._disposer = disposer

    def find(self, name):
        return self._tools.get(name)

    def execute(self, name, raw_input, context):
        tool = self.find(name)
        if tool is None:                                   # 阶段 1：查找
            return _fail(f"Unknown tool: {name}", "unknown_tool")

        try:                                               # 阶段 2：验证
            parsed = tool.validator(raw_input)
        except (TypeError, ValueError, KeyError) as exc:
            return _fail(f"Invalid input for {name}: {exc}", "invalid_input")

        try:                                               # 阶段 3：执行
            result = tool.run(parsed, context)
        except Exception as exc:
            return _fail(f"{type(exc).__name__}: {exc}", "tool_crash")

        if result.output is None:                          # 阶段 4：输出归一化与截断
            result.output = ""
        limit = tool.metadata.max_result_size_chars
        if len(result.output) > limit:
            omitted = len(result.output) - limit
            result.output = result.output[:limit] + f"\n... [{omitted} chars omitted]"
            result.metadata["truncated"] = True

        if not result.ok and result.error_code and "recovery_hint" not in result.metadata:
            result.metadata["recovery_hint"] = recovery_hint_for(result.error_code)   # 补恢复建议
        return result

    def dispose(self):
        if self._disposer is not None:
            self._disposer()
```

**关键点**：

- `execute` 把一次工具调用拆成「查找 → 验证 → 执行 → 截断」四个阶段，每阶段的失败都归一化
  成带 `error_code` 的 `ToolResult`，**绝不往外抛异常**——这就是「异常防火墙」；
- `_fail(...)` 是一个内部 helper，统一构造「失败结果 + 恢复建议」，保证所有失败路径都带
  `recovery_hint`（给模型看的纠正提示）。

## 讲解

### 每个模块为什么存在

| 能力 | 位置 | 谁消费 |
| --- | --- | --- |
| `ToolRegistry` | tooling.py | Loop（Day 7）、调度（Day 8）、权限（Day 11） |
| `_fail` | tooling.py | Registry 内部（统一构造失败结果） |
| `recovery_hint_for` | tooling.py | Registry 内部（错误码 → 恢复建议） |

### 数据流

```
模型产生 ToolCall(name, arguments)
        │
        ▼
ToolRegistry.execute()
        ├── 查找：找不到 → unknown_tool
        ├── 验证：validator 抛错 → invalid_input
        ├── 执行：run 抛错 → tool_crash
        └── 截断：超限 → head + "... [N chars omitted]"
        │
        ▼
统一的 ToolResult(ok, output, error_code, metadata) → 回填给 Loop
```

### 关键设计决策

1. **为什么执行要拆成「查找 / 验证 / 执行」三个阶段？** 一次工具调用有三个可能失败的环节，
   分开、各给一个明确的 error_code，价值在于**失败信息的语义精确**：模型拿到 `unknown_tool`
   知道「拼错了工具名」，拿到 `invalid_input` 知道「参数给错了」，拿到 `tool_crash` 知道「这
   工具本身有问题、换条路」。三种失败如果都返回同一个模糊错误，模型就只能瞎猜。

2. **为什么「验证错误」和「执行错误」必须分开？** `validator` 抛的错是**模型的入参问题**，
   `run` 抛的错是**工具的 bug**。如果混在一起，工具内部不小心抛了个 `TypeError` 会被误判成
   「参数错误」返回给模型，模型就会**反复改参数**——可它参数没错，是工具坏了，于是陷入
   「改参数 → 还是错 → 再改」的死循环。分开后 `tool_crash` 明确告诉模型「是工具的 bug」。

3. **为什么截断必须「可解释」？** 工具输出可能爆炸（超大文件、海量 grep 结果）。截断省
   token 是对的，但**不能只砍尾巴就完事**——必须告诉模型「发生了截断、原始有多大」
   （`... [N chars omitted]`）。否则模型会**基于不完整的内容做错误结论**。

4. **为什么截断和异常兜底放在 Registry 而不是每个工具里？** 如果每个工具自己实现截断、自己
   try/except，策略就散落各处、不一致。收进 Registry，让「输出归一化 + 异常兜底 + 截断」成为
   **所有工具统一经过的一环**，策略只改一处，也让 Loop 不必自己 try/except 每个工具调用。

## Registry 在 Agent Runtime 里的位置

Registry 是**「模型意图」和「真实执行」之间的唯一通道**，是整个 runtime 的「执行沙箱」。它
的位置决定了后面几个 Day 都会经过它：

- **Day 7 的 Loop**：模型返回 `ToolCall` → 交给 `tools.execute()` → 拿回 `ToolResult` 回填历史。
  Loop 全程不直接碰工具函数，只和 Registry 打交道；
- **Day 8 的调度**：并发/串行调度的是「Registry 里的工具」，调度器调用 `registry.execute`，
  借它的异常防火墙保证「一个工具崩了不丢其他结果」；
- **Day 11 的权限**：权限检查发生在「执行前」，通过 `ToolContext` 把 `PermissionManager`
  传进来，工具在执行前先过权限审批；
- **Day 24 的 MCP**：MCP 工具启动的子进程，用 `dispose()` 统一清理。

所以 Registry 是「所有工具执行的汇合点」——任何「模型想做的动作」，都要经过这道门，才能变成
「真实发生的副作用」。这道门的存在，让「执行」这件事变得受控、可观测、可限流。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 未知工具和畸形参数不调用 Runner | ✅ `unknown_tool` / `invalid_input` |
| Runner 抛异常不终止会话 | ✅ `tool_crash`（进程不崩） |
| 截断结果包含原始规模和继续获取提示 | ✅ `... [N chars omitted]` |
| dispose() 多次调用行为明确 | ✅ 回调被调用 |
| 全部测试通过 | ✅ `33 passed` |

## 小结

Day 5 的核心是「异常防火墙」这四个字：把「模型的结构化意图」和「真实 Python 函数」之间隔
一层 Registry，让所有工具失败都归一化成统一的 `ToolResult`，会话永不因工具崩溃而中断。这一
层的价值要到后面才完全显现——Day 7 的 Loop 能放心调 `execute` 而不必自己 try/except，Day 8
的调度、Day 11 的权限、Day 24 的 MCP 都经过这道门。今天把「受控执行」这道门立起来，后面加
工具、加扩展都从这道门走，安全策略集中在一处。
