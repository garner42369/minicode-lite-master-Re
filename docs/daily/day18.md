# Day 18：压缩与工作记忆——减小历史，但不丢任务状态

## 今日任务

轻量裁剪（Day 17 的 micro_compact）只做确定性去重，历史一长就撑爆上下文。今天实现
**摘要压缩 + 受保护工作记忆**：把旧历史摘要化，同时保证「任务目标、约束、验证状态」
这些关键事实跨压缩存活。

## 任务分析

**核心结论：记忆注入和压缩摘要是两个独立动作，必须拆成两条线。**

- **记忆**（目标/约束/验证状态）是「当前任务状态快照」，每轮都在变，应该**每轮发请求前
  作为 system 消息注入**；
- **压缩**是「历史太长时把旧对话摘要化」，只动对话历史，**偶发**（超阈值才触发）。

我们最初把 `protected.render()` 直接拼进 `compact_messages` 的 summary 字符串里，等于把
「记忆注入」塞进了「压缩」，导致「保护记忆包含哪些」不可控——因为它取决于「压缩那一刻
render 了什么」。修正方式是拆开：记忆走 `upsert_stable_task_message` 独立 system 消息，
压缩只动对话历史、system 消息原样保留（详见 `docs/memory-context-mixing.md`）。

## 代码实现

### 1. 受保护记忆：StableTaskPack（`turn_kernel.py`）

```python
@dataclass(frozen=True, slots=True)
class StableTaskPack:
    goal: str = ""
    constraints: tuple[str, ...] = ()
    verification: str = ""          # 验证状态文本，空 = 尚未修改

    def to_protected_text(self) -> str:
        lines = []
        if self.goal:
            lines.append(f"Goal: {self.goal}")
        lines += [f"Constraint: {c}" for c in self.constraints]
        if self.verification:
            lines.append(f"Verification: {self.verification}")
        return "\n".join(lines)
```

**策略**：`stable_task_pack(task, state)` 每轮从 `task` + `TurnState` **重新构建**快照（不
累积）——验证状态这类「当前」事实每次都重算，而不是堆进存储。

### 2. 记忆注入：upsert_stable_task_message（`turn_kernel.py`）

```python
STABLE_TASK_STATE_MARKER = "[Stable task state]"

def upsert_stable_task_message(messages, stable_text):
    filtered = [m for m in messages
                if not (m["role"] == "system"
                        and str(m.get("content", "")).startswith(STABLE_TASK_STATE_MARKER))]
    filtered.append({"role": "system", "content": f"{STABLE_TASK_STATE_MARKER}\n{stable_text}"})
    return filtered
```

**策略**：记忆是**一条独立的 system 消息**，带固定标记。upsert = 先删旧的、再 append 新的，
消息列表里永远只有一条记忆消息，每轮覆盖更新、不累积。

### 3. 压缩摘要：compact_messages 五字段（`context_compactor.py`）

```python
def compact_messages(messages, keep_recent):
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if len(non_system) <= keep_recent:
        return list(messages)
    cut = _safe_cut(non_system, len(non_system) - keep_recent)
    removed, recent = non_system[:cut], non_system[cut:]
    # 从 removed 确定性提取五字段（不调 LLM）：
    # topics（user 消息）、tools（工具名）、files（input.path）、
    # errors（is_error 内容）、unlinked（无链接 tool_result 的 head/tail）
    ...
    return [*system_msgs, summary, *recent]
```

**策略**：只压缩对话历史（non-system），**system 消息原样保留**——记忆作为 system 消息注入，
因此永不进摘要。摘要从被压缩的消息里**确定性提取**（不调 LLM），五字段：
`topics / tools / files / errors / unlinked`。

### 4. 无链接工具结果进摘要（unlinked）

```python
linked_calls = {m.get("tool_call_id") for m in removed
                if m.get("role") == "assistant_tool_call" and (m.get("input") or {}).get("path")}
unlinked = [_summarize_content(m.get("content", "")) for m in removed
            if m.get("role") == "tool_result"
            and m.get("tool_call_id") not in linked_calls
            and m.get("content") and not m.get("is_error")]
```

**策略**：不调 LLM 判断「重不重要」，只看「有没有链接」——有 `path` 的结果靠重读恢复（只放
路径），无链接的一次性输出（run_command/search）一律 head/tail 进摘要，否则永久丢失。

### 5. 压缩熔断：CompactionCircuitBreaker（`circuit_breaker.py`）

```python
class CompactionCircuitBreaker:
    def record_success(self): ...   # 清零连续失败
    def record_failure(self): ...   # 连续失败 +1，达阈值熔断
    def is_allowed(self) -> bool: ...
    def reset(self): ...
```

**策略**：连续 3 次压缩失败熔断。为什么确定性压缩也要熔断——「失败」的另一种形态是「压了
也没用」（历史有超大消息，压缩后 usage 仍超阈值），会形成「每轮都压但压不下来」的死循环。

### 6. 响应式截断：reactive_truncate（`context_compactor.py`）

```python
def reactive_truncate(messages, keep_ratio=0.4):
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    keep_count = max(3, int(len(non_system) * keep_ratio))
    marker = {"role": "system", "content": "[Context aggressively truncated for recovery]\n..."}
    return [*system_msgs, marker, *non_system[-keep_count:]]
```

**策略**：比 compact_messages 更激进——不留摘要，直接砍掉最旧历史，只留最近 40%，熔断后
兜底用（宁可丢历史也不死循环）。

### 7. 分层上下文：LayeredContext（`layered_context.py`）

```python
class ContextLayer(str, Enum):
    SYSTEM = "system"; PROJECT = "project"; SESSION = "session"; SCRATCHPAD = "scratchpad"

class ContextBudget:
    total_limit = 8000
    # system 15% / project 25% / session 45% / scratchpad 15%

class LayeredContext:
    def add(self, layer, text, *, priority=0): ...   # 超预算按 priority 淘汰
    def get_all_text(self): ...                       # SYSTEM → ... → SCRATCHPAD 组装
```

**策略**：working_memory 解决「保护什么」，layered_context 解决「怎样装进请求」——四层各
自预算，超了按 priority（高的优先保留、同 priority 越旧越先淘汰）。

### 8. 接入 agent_loop（发请求前）

```python
for step_idx in range(max_steps):
    if state is not None:
        state.step = step_idx
        usage = sum(estimate_tokens(str(m)) for m in history)
        if usage > DEFAULT_CONTEXT_WINDOW * _COMPACT_RATIO:   # token 高水位触发
            if compaction_breaker.is_allowed():
                history = compact_messages(history, _COMPACT_KEEP_RECENT)
                # 压缩有效 record_success + on_compact，无效 record_failure
            else:
                history = reactive_truncate(history)           # 熔断后保底截断
        history = upsert_stable_task_message(history, stable_task_pack(task, state).to_protected_text())
    step = model.next(history)
```

**策略**：顺序必须「先压缩、后注入」——压缩只动对话历史，注入的记忆作为 system 消息在压缩
时不参与，保证记忆永远是「最新状态」且不混进摘要。

## 讲解

### 数据流

```
任务 + TurnState ──stable_task_pack──▶ StableTaskPack 快照（每轮重建）
快照.to_protected_text() ──upsert_stable_task_message──▶ 独立 system 消息（记忆）
对话历史(超阈值) ──compact_messages──▶ 五字段摘要（system 消息保留）
压缩无效连续 3 次 ──circuit_breaker──▶ 熔断 → reactive_truncate 激进截断
压缩发生 ──on_compact 回调──▶ 压缩事件（供调用方持久化到 Session）
分层内容 ──LayeredContext──▶ 按层预算组装请求
```

### 关键设计决策

1. **为什么记忆/摘要必须拆线？** 记忆是「状态」（每轮变），摘要是「历史回顾」（偶发）。
   混在一起让「保护记忆包含哪些」不可控，且记忆被冻结在压缩那一刻、无法每轮更新。
2. **为什么摘要确定性提取、不调 LLM？** 课件 Day 17 末尾明确「先不要实现 LLM 摘要压缩」，
   参考项目 `_generate_structured_summary` 也不调 LLM。「已完成/未完成」这类语义字段确定性
   规则提不出来，留待后续 TUI 阶段（task_tracker）再补。
3. **为什么压缩触发用 token 而非消息数？** 一条大 tool_result 和一条短 user 都算「1 条」，
   消息数不可靠；用 `estimate_tokens` 估算 + 窗口比例（0.8）触发才准确。
4. **为什么熔断后激进截断？** 压缩无效还反复压会死循环，熔断保证「有限次结束」，兜底用
   激进截断（宁可丢历史）。
5. **为什么原始 Session 不被压缩覆盖？** agent_loop 内部 `history = list(messages)` 拷贝，
   压缩只动内部 history；调用方的完整 messages 保持完整，压缩事件通过 `on_compact` 回调
   单独持久化到 Session.events。

### 易错点

压缩摘要本身也消耗上下文。若每次把旧摘要再次全文摘要，信息逐轮退化。我们保留
`source_range` 标记被压缩的原始范围，且 system 消息（含记忆）永远原样保留，避免退化。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 记忆与摘要拆线（记忆独立 system 消息，不进摘要） | ✅ `upsert_stable_task_message` + system 保留 |
| Tool Call/Result 始终配对 | ✅ `_safe_cut` 不拆工具对 |
| 原始 Session 未被摘要覆盖 | ✅ 内部拷贝 + `on_compact` 回调单独持久化 |
| 连续压缩失败有限次结束 | ✅ `CompactionCircuitBreaker` 阈值 3 + `reactive_truncate` |
| 工作记忆超预算按策略淘汰 | ✅ `WorkingMemory.evict_by_budget`（importance 优先） |
| 分层分配预算 | ✅ `LayeredContext` 四层 + priority 淘汰 |
| 既有测试无回归 | ✅ 103 passed |
