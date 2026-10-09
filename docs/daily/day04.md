# Day 4：Mock Model——用确定性剧本替代真实模型

## 今日任务

建立两个离线模型替身，让后续 Agent Loop 的测试与演示不依赖网络、费用和模型随机性：

- `ScriptedModel`（剧本式）：测试传入步骤队列，模型每次 `next()` 取下一步；
- `MockModelAdapter`（命令式）：解析 `/ls`、`/read`、`/cmd` 等快捷命令，用于手工 Demo。

## 任务分析

**为什么必须先有 Mock？** Day 7 就要写 Agent Loop 了，而 Loop 的核心是「调模型」。如果
没有 Mock，测试 Loop 就得真实调用模型 API——三个致命问题：

| 问题 | 后果 |
| --- | --- |
| 花钱 | 每跑一次测试都消耗 token |
| 慢 | 等网络往返，测试从秒级变分钟级 |
| 不稳定 | 模型输出随机，同一测试这次过下次挂 |

Mock 把「模型的下一步」从「不可控的随机输出」变成「我们预先指定的确定输出」，让测试确定、
可重复、零成本。

**为什么需要两种 Mock？** 测试和演示的需求不同：**测试**要精确编排「工具 → 结果 → 下一步」
的复杂序列，还要检查「Loop 回填的历史对不对」；**手工 Demo** 要能「敲一个命令、看到对应
行为」。一个偏「精确编排」（剧本式），一个偏「交互演示」（命令式），所以拆成两个。

## 代码实现

### 1. ScriptedModel（剧本式）—— 步骤队列 + 消息快照

```python
class ScriptedModel:
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = iter(steps)                              # 步骤队列
        self.received_messages: list[list[ChatMessage]] = []   # 记录每次收到的历史

    def next(self, messages, on_stream_chunk=None, store=None) -> AgentStep:
        self.received_messages.append(deepcopy(messages))      # 深拷贝快照
        try:
            step = next(self._steps)
        except StopIteration as exc:
            raise RuntimeError("ScriptedModel steps exhausted") from exc   # 耗尽报错
        if on_stream_chunk and step.content:
            on_stream_chunk(step.content)
        return step
```

**关键点**：

- `received_messages.append(deepcopy(messages))` 是核心验收——记录每次调用收到的历史，
  测试才能断言「第二次调用看到了第一次的工具结果」。`deepcopy` 是因为 messages 是列表、
  后续会被外部 append，必须冻结「那一刻」的历史；
- `except StopIteration ... raise RuntimeError` 让「步骤耗尽」显式报错，而不是悄悄重复
  最后一步（否则会掩盖「Loop 多调用了模型」的 bug）。

### 2. MockModelAdapter（命令式）—— 解析快捷命令

```python
def _last_user_message(messages):
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _last_tool_result(messages):
    for message in reversed(messages):
        if message.get("role") == "tool_result":
            return message
    return None


class MockModelAdapter:
    def next(self, messages, on_stream_chunk=None, store=None) -> AgentStep:
        # 1. 先看有没有工具结果 → 有就总结（完成「工具 → 模型」回环）
        tool_result = _last_tool_result(messages)
        if tool_result is not None:
            return AgentStep(type="assistant", kind="final",
                             content=f"[{tool_result['tool_name']}] 结果：\n{tool_result['content']}")

        # 2. 没有工具结果 → 机械解析用户快捷命令
        user_text = _last_user_message(messages).strip()
        tool_id = f"mock-{int(time.time() * 1000)}"
        if user_text == "/tools":     return AgentStep(type="assistant", kind="final", content="...帮助...")
        if user_text.startswith("/ls"):    return AgentStep(type="tool_calls", calls=[ToolCall(tool_id, "list_files", ...)])
        if user_text.startswith("/read "): return AgentStep(type="tool_calls", calls=[ToolCall(tool_id, "read_file", ...)])
        if user_text.startswith("/cmd "):  return AgentStep(type="tool_calls", calls=[ToolCall(tool_id, "run_command", ...)])
        return AgentStep(type="assistant", kind="final", content="...帮助...")
```

**关键点**：

- 入口逻辑是「**先看有没有工具结果**」——有就直接总结（完成「工具 → 模型」回环），没有才
  解析命令。这是为了手工 Demo 能跑出完整闭环；
- 命令解析是纯机械的字符串匹配（`/read` 触发 `read_file`），不做任何「智能」——越笨越确定，
  越适合验证 Runtime 本身。

## 讲解

### 每个模块为什么存在

| 能力 | 位置 | 谁消费 |
| --- | --- | --- |
| `ScriptedModel` | mock_model.py | 测试（精确编排复杂序列 + 断言历史回填） |
| `MockModelAdapter` | mock_model.py | 手工 Demo（命令 → 工具调用） |
| `_last_user_message` / `_last_tool_result` | mock_model.py | MockModelAdapter 内部（从历史里找消息） |

### 数据流

```
【测试】ScriptedModel 步骤队列 ──next()──► AgentStep（预先指定的下一步）
【Demo】用户敲 "/read README.md" ──MockModelAdapter 解析──► ToolCall(read_file)
                │                                              │
                ▼                                              ▼
           Loop 执行工具 ──► 结果回填历史 ──► 再调 Mock（此时 Mock 看到工具结果，生成总结）
```

关键点：两种 Mock 都「塞进同一个 `ModelAdapter` 协议」里，Loop 看到的数据流完全一样——
`messages 进 → AgentStep 出`。区别只在「AgentStep 怎么来的」（弹队列 vs 解析命令）。

### 关键设计决策

1. **为什么 ScriptedModel 要记录消息快照？** 这是 Day 4 最核心的验收：「能断言第二次模型
   调用已经看到第一次工具结果」。如果 Mock 只是「弹队列」，它只证明自己会 `next(iter)`，
   不能证明 **Loop 回填了正确的历史**。记录 `received_messages` 后，测试才能检查「历史里
   真的包含第一次的工具结果」，这才验证了「模型 ↔ 工具」的闭环。

2. **为什么耗尽要报错而不是重复最后一步？** 如果「悄悄重复最后一步」，会掩盖「Loop 多调用
   了模型」的 bug——剧本 3 步、Loop 调了第 4 次，Mock 重复返回第 3 步，测试「看着都过」。
   显式抛错让「多调用」立刻爆炸。Mock 要**暴露调用方的 bug，而不是帮调用方藏 bug**。

3. **Mock 的本质是什么？** 是「控制状态迁移，不是模拟聪明」。一个常见误区是把 Mock 做得
   「像真模型一样聪明」——那样测试就依赖「Mock 的聪明程度」，变得和依赖真实模型一样不可
   重复。Mock 回答的问题是「给定这段历史，下一步该是什么」，所以只做机械的字符串解析。

4. **为什么两种 Mock 都实现同一个 next() 协议？** 两个类的 `next(messages, on_stream_chunk,
   store)` 签名和 Day 2 的 `ModelAdapter` 协议完全一致。因为 Loop 不关心背后是哪种 Mock 还是
   真实模型，它只认 `next()`。两种 Mock 实现同一协议，就能在测试/演示里无缝替换真实模型，
   Loop 代码一行不改——这是「面向协议编程」：上层依赖接口，不依赖具体实现。

## Mock 在 Agent Runtime 里的位置

Mock 不是「测不起真实模型才用的权宜之计」，它是**「确定性」这个属性**的提供者：

- **对 Loop 而言**：Mock 和真实模型（Day 10）在 `ModelAdapter` 协议上是「同一个东西」——Loop
  只认 `next()`，不关心背后是「弹队列」还是「发 HTTP 请求」；
- **对测试而言**：Mock 让「Loop 的行为」可以被精确断言——不用等网络、不用花钱、不用面对
  随机输出；
- **对 Day 10 的接入而言**：Day 10 的 `build_adapter` 工厂里 `model == "mock"` 返回
  `MockModelAdapter`，`model == 真实模型` 返回 `OpenAIAdapter`——Day 4 立的这两个 Mock，Day 10
  直接作为「离线路径」接进工厂。

所以 Mock 的意义在于：**它让「模型」这一环变得可控、可替换**。开发、测试、演示全程用 Mock
（快、稳、不花钱），上线时工厂一切换，就换成真实模型——切换点只在 `build_adapter` 一处。

### 易错点：Mock 只能验证「状态迁移」，不能验证「推理质量」

Mock 能验证的是「给定这段历史，Loop 会不会走到正确的下一步」（状态迁移对不对），但它
**永远不能验证「真实模型的推理质量」**——真实模型会不会真的读懂文件、真的做出正确的工具
调用，这些 Mock 测不出来。

所以 Mock 和真实测试是**互补**的，不是替代：开发阶段用 Mock 验证「Runtime 逻辑对不对」
（快、稳、确定），上线前用真实模型验证「模型行为对不对」（Day 10 的 real_model_demo）。
如果把 Mock 当成「真实模型的等价替身」、只测 Mock 就上线，就会漏掉「模型实际不会用工具」
这类真实问题——这是 Day 10 之后反复强调「真实测试」的原因。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 完全离线执行（无网络、无 API key） | ✅ |
| 能断言第二次模型调用已看到第一次工具结果 | ✅ `test_scripted_model_records_message_history` |
| 步骤耗尽定义明确错误 | ✅ `test_scripted_model_exhaustion_raises` |
| 覆盖工具请求 / 最终文本 / 进度 / 空响应 / 异常 | ✅ 核心剧本已覆盖 |
| 全部测试通过 | ✅ `27 passed` |

## 小结

Day 4 的核心是理解「Mock 的价值是确定性，不是省钱」。两个 Mock 一个偏「精确编排」（剧本式，
能断言历史回填）、一个偏「交互演示」（命令式，能跑 Demo），但都实现同一个 `next()` 协议，
所以能在测试/演示里无缝替换真实模型。更关键的是 Mock 的「暴露 bug 而非掩盖 bug」的设计
——记录消息快照、耗尽报错——让 Day 7 的 Loop 测试真正能查出问题。理解了 Mock 是「模型这一
环的可控替身」，Day 10 接真实 Provider 时，就会明白「工厂里 mock/真实 两条路径」是怎么来的。
