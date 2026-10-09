# Day 8：错误恢复、多工具与调度——让循环在真实世界里不脆弱

## 今日任务

把 Day 7 的「理想循环」升级成「可恢复运行时」。真实模型会请求未知工具、给错参数、返回空
响应、一次请求多个工具。今天加四样东西：并发调度（只读并发、有副作用串行）、错误分类与
恢复建议、有限恢复（空响应重试、连续失败停止）、指标记录。

## 任务分析

**为什么调度只做「分组 + 执行 + 回填 + 记录」？** 课件易错点：别在 Day 8 就做熔断、自适应
并发——那些是「根据指标做决策」，属于 Day 27 控制论。Day 8 的调度只回答一个确定性问题——
「哪些能并发、哪些必须串行」，依据是工具自己声明的 `concurrency_safe` 能力标志，而不是
「凭感觉调并发」。

**为什么错误分类用 error_code 而非关键词匹配？** 靠匹配 output 文本里的关键词（`"connection"`、
`"timeout"`）来分类是脆弱的——换个措辞就误判。而 `ToolResult.error_code` 是工具/Registry 显式
声明的结构化信号，可靠得多。

## 代码实现

### 1. execute_calls —— 分组 + 并发/串行 + 按原索引回填

```python
def execute_calls(calls, registry, context) -> SchedulerResult:
    # 按「是否并发安全」分组
    parallel = [(i, c) for i, c in enumerate(calls) if _is_concurrency_safe(registry, c.name)]
    serial   = [(i, c) for i, c in enumerate(calls) if not _is_concurrency_safe(registry, c.name)]
    completed = {}

    if parallel:   # 只读工具：线程池并发
        with ThreadPoolExecutor(max_workers=min(4, len(parallel))) as pool:
            for future in as_completed([pool.submit(run, item) for item in parallel]):
                i, call, result, duration = future.result()
                completed[i] = (call, result)          # 用原始索引 i 记录，不管完成先后
                metrics.append(ToolMetric(call.name, duration, result.ok, result.error_code))
    for item in serial:   # 有副作用工具：串行
        i, call, result, duration = run(item)
        completed[i] = (call, result)

    # 关键：按原始索引重建，保证回填顺序 = 模型发出的顺序
    return SchedulerResult([completed[i] for i in range(len(calls))], metrics)
```

**关键点**：

- `completed` 用原始索引 `i` 作键，最后 `[completed[i] for i in range(len(calls))]` 重建——
  并发时工具完成顺序是乱的，但回填必须恢复成「模型发出的顺序」；
- 并发只对「声明了 `concurrency_safe`」的只读工具；有副作用的工具必须串行，避免副作用竞态。

### 2. 空响应重试 + 连续失败停止（在 agent_loop 里）

```python
if step.type == "assistant" and not step.content.strip():   # 空响应
    empty_responses += 1
    if empty_responses >= empty_response_retry_limit:
        return history, "empty_response"
    history.append({"role": "user", "content": _NUDGE_AFTER_EMPTY})   # 注入唤醒，重试
    continue

# 工具结果处理时追踪「同工具连续失败」
if not result.ok:
    consecutive_failures = consecutive_failures + 1 if call.name == last_failed_tool else 1
    last_failed_tool = call.name
else:
    consecutive_failures = 0; last_failed_tool = None
if consecutive_failures >= max_consecutive_failures:
    return history, "max_failures"
```

## 讲解

### 每个模块为什么存在

| 能力 | 位置 | 谁消费 |
| --- | --- | --- |
| `execute_calls` | scheduler.py | Loop（执行一批工具调用） |
| `ToolMetric` | scheduler.py | 观测层（Day 27 控制论用指标） |
| 空响应重试 / 连续失败停止 | agent_loop.py | Loop 内部恢复 |

### 数据流

```
Loop 拿到 step.calls（一批工具调用）
        │
        ▼
execute_calls 调度器
        ├── 分组：concurrency_safe 的进 parallel，其余进 serial
        ├── parallel：线程池并发，as_completed 按完成顺序拿结果
        ├── serial：串行逐个执行
        └── 按原始索引重建 observations（顺序 = 模型发出的顺序）
        │
        ▼
SchedulerResult(observations, metrics) → Loop 回填历史 + 观测层消费指标
```

### 关键设计决策

1. **为什么回填必须恢复「原调用顺序」？** 并发执行时慢的工具后完成、快的先完成，
   `as_completed` 拿到的是**完成顺序**，但模型是根据它**发出调用的顺序**来理解结果的。
   所以用原始索引重建——否则模型把第 2 个工具的结果当成第 1 个的，张冠李戴。

2. **为什么调度不捕获工具异常？** Day 5 的 `ToolRegistry.execute` 已经是「异常防火墙」——
   任何工具崩溃都被归一化成 `ToolResult(ok=False, error_code="tool_crash")`，不会往外抛。所以
   `future.result()` 不会抛异常，「一个工具崩溃不丢失其他调用结果」这一条在 Day 5 就保证了。

3. **为什么恢复建议是「系统注记」而非「用户消息」？** 失败结果的恢复建议附在 `tool_result`
   里，而不是单独发一条 `role: "user"` 消息。因为 `tool_result` 本来绑定上一次 `tool_call` 的
   id，恢复建议附在这里，模型读到的是「这次工具动作的结果 + 环境对这次动作的纠正」，因果链
   连续。若伪装成 user 消息，模型会把运行时 steering 误当成「用户的新指令」。

4. **为什么是「同工具连续失败」而非「累计失败」？** 防死循环的目标是「模型反复调用同一个
   失败的工具」。如果累计所有失败，模型**交替**尝试不同工具（正常的探索行为）也会被误判成
   死循环。只有「同一个工具连续失败」才是「踩同一个坑」的精确信号。

## 调度在 Agent Runtime 里的位置

调度器夹在 **Loop 和 Registry 之间**：Loop 拿到模型的一批 `ToolCall`，交给调度器；调度器根据
工具的 `concurrency_safe` 标志决定并发还是串行，再逐个调用 `registry.execute`。

它的职责边界很窄——**只做「分组 + 执行 + 回填 + 记录」，不做「根据指标做决策」**。这个边界
很重要，因为它为 Day 27 的控制论留好了数据：调度器记录每次调用的「名称、耗时、成败、错误
类别」（`ToolMetric`），但**不据此调并发、不熔断**。等 Day 27 控制论来了，它消费这些指标做
决策，调度器本身不用改。所以 Day 8 的价值不只是「能并发」，更是把「观测（记录指标）」和
「控制（做决策）」分开了。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 两个只读慢工具总耗时接近一个而非两倍 | ✅ `test_concurrent_readonly_tools_finish_in_parallel` |
| 有副作用的工具从不并发 | ✅ `test_serial_tools_do_not_overlap` |
| 并发完成顺序不同，回填顺序仍稳定 | ✅ `test_observations_preserve_call_order` |
| 一个工具抛异常不丢失其他调用结果 | ✅ `test_one_crash_does_not_lose_others` |
| 失败工具指标带错误类别 | ✅ `test_metrics_record_error_code` |
| 空响应有限重试 / 达上限停止 | ✅ 两个测试 |
| 同工具连续失败达阈值停止 | ✅ `test_consecutive_failures_stop` |
| 全部测试通过 | ✅ `60 passed` |

## 小结

Day 8 的核心是把 Day 7 的「理想循环」升级成「真实世界里不脆弱的运行时」——用「并发调度、
错误分类、有限恢复、指标记录」四样东西接住真实模型的乱来。关键的设计克制是：**调度只做
「分组 + 执行 + 回填 + 记录」，不做「根据指标做决策」**——后者是 Day 27 控制论的事。今天把
「观测」和「执行」分开，为未来的智能控制留好数据，而不是提前把控制逻辑塞进来。
