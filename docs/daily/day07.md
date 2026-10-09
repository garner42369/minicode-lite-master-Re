# Day 7：最小 Agent Loop——真正跑通「模型—工具—模型」闭环

## 今日任务

实现 `run_agent_turn`：第一次把前 6 天的零件组装成**真正工作的闭环**。前六天只有零件
（协议、Mock、Registry、只读工具），今天让它们协同起来：

```
模型请求工具 → 运行时执行工具 → 结果回填历史 → 模型根据结果继续 → ... → 最终回答
```

## 任务分析

**为什么这是「心脏」？** 普通 LLM 调用是「用户问题 → 模型 → 文本回答」，而 Agent Loop 是
「模型和工具交替工作」。之前所有模块（`types`/`mock_model`/`tooling`/`tools`）都是为这个
闭环服务的零件，今天第一次组装成一个「能自己转起来」的循环。

**为什么不能一次性堆满功能？** 课程易错点：不要现在就把记忆、控制论、模型路由全塞进 Loop。
先保住一份 100~200 行**可工作的核心**；后续复杂功能都能解释为「这个闭环前后的附加策略」。

## 代码实现

### 1. run_agent_turn —— 模型—工具—模型闭环

```python
def run_agent_turn(model, tools, messages, cwd, *, max_steps=10,
                   on_tool_start=None, on_tool_result=None, on_assistant_message=None):
    history = list(messages)          # 复制输入，不污染调用方的列表
    context = ToolContext(cwd)
    for step_idx in range(max_steps):
        step = model.next(history)    # 问模型下一步

        if step.type == "assistant":  # 文本分支
            role = "assistant_progress" if step.kind == "progress" else "assistant"
            history.append({"role": role, "content": step.content})
            if step.kind != "progress":
                return history, "done"   # 最终回答 → 结束
            continue                     # 进度播报 → 继续循环

        for call in step.calls:        # 工具分支
            result = tools.execute(call.name, call.arguments, context)   # 经 Registry 执行
            history.append({"role": "assistant_tool_call", "tool_call_id": call.id,
                            "tool_name": call.name, "input": call.arguments})   # 请求
            history.append({"role": "tool_result", "tool_call_id": call.id,
                            "tool_name": call.name, "content": result.output,
                            "is_error": not result.ok})                         # 结果

    return history, "max_steps"       # 预算耗尽
```

**关键点**：

- 循环只有两个分支：`assistant`（文本）和 `tool_calls`（工具），每次 `model.next(history)`
  之后，要么判断「结束还是继续」，要么执行工具并回填；
- 一次工具调用写**两条历史**（`assistant_tool_call` 请求 + `tool_result` 结果），用同一个
  `tool_call_id` 配对。

## 讲解

### 每个模块为什么存在

| 能力 | 位置 | 谁消费 |
| --- | --- | --- |
| `run_agent_turn` | agent_loop.py | 上层入口（Day 21 装配）、测试 |

### 数据流

```
messages（初始历史）
    │
    ▼
model.next(history) ──► AgentStep
    ├── assistant + final      → 结束，返回 (history, "done")
    ├── assistant + progress   → 记录，继续循环
    └── tool_calls             → tools.execute(每个 call)
                                    ├── assistant_tool_call（请求）
                                    └── tool_result（结果）
                                    → 回填 history → 再问 model
```

### 关键设计决策

1. **为什么工具调用要写「两条历史」？** 一次工具调用产生两条消息：`assistant_tool_call`
  （模型要求了什么）和 `tool_result`（环境返回了什么），用**同一个 `tool_call_id`** 配对。
  这是 Provider 协议的硬要求，也是闭环正确性的关键：只有结果没有请求，模型不知道「这是谁
  要求的、为什么」；只有请求没有结果，模型不知道「动作是否成功」。

2. **为什么复制输入（history = list(messages)）？** `messages` 是调用方传入的列表。如果直接
  `append`，调用方会在不知情时看到自己的列表被污染，而且无法用同一份 `messages` 重复运行
  多次（比如评测里跑 100 次同一个场景）。

3. **为什么区分「进度」和「最终」？** 模型在长任务里会先发「我正在读取文件…」再发「完成」。
  如果不区分，Loop 会在**进度播报后错误地提前结束**——用户只看到一半。`progress` → 记录、
  继续循环；`final` → 记录、结束。

4. **为什么工具执行走统一的 tools.execute？** 所有工具调用都走 `tools.execute(...)`，这个
  「统一入口」是安全执行的核心：不管工具是内置的还是未来的 MCP/子代理，都经过这一道门，
  **统一**施加参数校验、异常兜底、输出截断。模型只能「提议动作」，动作在沙箱里跑，失败被
  兜底成 ToolResult。

## Loop 在 Agent Runtime 里的位置

Loop 是整个 runtime 的**心脏**，后面几乎每个 Day 都是「在这个闭环的前后加策略」：

- **Day 8**：在「工具执行」这一步加并发调度、错误恢复；
- **Day 10**：把 `model` 从 Mock 换成真实 Provider（Loop 代码一行不改，因为只认 `next()`）；
- **Day 11**：在「工具执行前」加权限审批（通过 ToolContext 传入 permissions）；
- **Day 14**：在「模型返回 final 时」加 Turn Kernel 的验证门槛（决定「能不能真的结束」）；
- **Day 16**：在「开始前」加 replay/resume 的恢复逻辑。

所以 Day 7 的这一小段循环，是后面所有能力的「挂载点」。理解了这个循环，后面每一天都能解释
成「在这个闭环的哪个环节、加了一个什么策略」。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 第二次模型调用确实收到第一次工具结果 | ✅ `test_tool_result_is_fed_back` |
| 工具失败也会回填 | ✅ `test_tool_failure_is_fed_back`（`is_error=True`） |
| 进度消息不会让循环提前结束 | ✅ `test_progress_does_not_end_loop` |
| 达到最大步数时没有额外模型请求 | ✅ `test_max_steps_stops_without_extra_call` |
| 回调关闭时核心行为不变 | ✅ 多数测试不传回调仍正常 |
| 全部测试通过 | ✅ `51 passed` |

## 小结

Day 7 是整个项目第一个「真正工作的闭环」，也是后续所有能力的地基。核心就一句话：**模型和
工具交替工作，结果回填历史，直到模型给出最终回答**。今天确立的几个设计——工具调用写两条
历史、复制输入、区分进度与最终、统一执行入口——会成为 Day 8（恢复）、Day 14（Turn Kernel）、
Day 16（replay/resume）反复依赖的基础。理解了「Loop 是心脏、其他都是挂在上面的策略」这个
定位，后面每一天都能快速抓住重点。
