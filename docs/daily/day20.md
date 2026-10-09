# Day 20：记忆流水线——高级记忆能力如何组合

## 今日任务

Day 19 建了 `MemoryManager`（记忆的地基），今天把它包装成 **`MemoryPipeline` 统一外观**，
再接上可插拔的高级记忆模块（重排、向量、时间线）——同时明确**哪些是主产品必需、哪些是
可选研究能力**。今天是「核心能力阶段」的**收官节点**。

## 任务分析

### 为什么 MemoryPipeline 很重要（课件原话）

没有统一外观，Agent Loop 会直接 import 七个记忆模块，任何内部替换都会扩大影响面。Loop
应该只知道：

```
pipeline.inject(task, budget)      # 注入
pipeline.feedback(outcome)         # 反馈
pipeline.write(verified_fact)      # 写入
pipeline.maintain()                # 维护
```

**外观模式的价值**：Loop 只依赖这四个动词，不关心背后是 BM25 还是向量、有没有 reranker。
内部换检索算法、加插件，都不动 Loop——这是「把复杂度关进一个类」的典型做法。

### 注入策略：随上下文压力降级

记忆注入是**每轮请求都做**的，但它只是「辅助」，不喧宾夺主。所以注入量要随上下文压力
反向变化：

| 上下文压力 | 注入策略 | 含义 |
| --- | --- | --- |
| >= 90% | off | 临界，不注入（上下文快满了，再塞记忆挤掉当前对话） |
| >= 75% | summary | 高压，只注入 2 条、每条 120 字摘要 |
| < 75% | full | 低压，注入 5 条、每条 500 字全文 |

### 高级能力：可插拔、有 fallback

课件表格把七个模块分成「必需 / 推荐 / 可选 / 专用」：

| 模块 | 作用 | 默认 |
| --- | --- | --- |
| memory_pipeline | 统一生命周期外观 | **必需** |
| memory_injector | 按上下文压力决定注入多少 | 推荐 |
| memory_reranker | LLM 对初筛重排 | 可选，昂贵需降级 |
| vector_memory | 稀疏/向量相似度，与 BM25 合并 | 可选 |
| memory_curator_agent | 后台合并、过期、冲突策展 | 可选 |
| agent_reflection | 任务后提炼可复用经验 | 可选且需验证 |
| timeline_memory | 事件顺序、最新状态、日期推理 | 专用研究能力 |

核心原则：**高级模块必须可关闭、有 fallback**。reranker 失败回退 BM25、vector 缺依赖降级、
timeline 不污染普通记忆——禁用所有高级模块时，主流程照常工作。

## 代码实现

### 1. 注入决策（`decide_injection`）

```python
def decide_injection(context_ratio: float) -> InjectionDecision:
    if context_ratio >= 0.90:
        return InjectionDecision("off", 0, 0)
    if context_ratio >= 0.75:
        return InjectionDecision("summary", 2, 120)
    return InjectionDecision("full", 5, 500)
```

**策略**：注入量随上下文压力反向——压力越大注入越少。这是「记忆是辅助，不喧宾夺主」的
量化表达。

### 2. 注入（`inject`，含 rerank 降级 + cooldown）

```python
def inject(self, query, context_ratio) -> str:
    decision = decide_injection(context_ratio)
    if decision.mode == "off":
        return ""
    # cooldown：相同任务（query hash 相同）冷却期内不重复注入
    if key == self._last_inject_key and now - self._last_inject_time < self.cooldown_seconds:
        return ""
    candidates = self.manager.search(query, limit=10)
    if self.reranker is not None:
        try:
            candidates = self.reranker(query, candidates)
        except Exception:
            pass  # rerank 失败，保留 BM25 基础排序（降级，不中断注入）
    selected = candidates[: decision.max_entries]
    return "\n".join(f"- [{e.source}] {e.content[:decision.max_chars_each]}" for e in selected)
```

**策略**：
- **rerank 降级**：reranker 抛异常时 `pass`，保留 BM25 基础排序——重排失败不该中断注入；
- **cooldown**：相同任务在冷却期内不重复注入同一批记忆，避免「同一个任务反复问、反复注入
  一模一样的东西」。

### 3. 已验证写入（`write_verified_fact`）

```python
def write_verified_fact(self, content, *, source, tags, verified=True):
    if not verified:
        raise ValueError("unverified reflection cannot become project memory")
    entry = self.manager.add(MemoryScope.PROJECT, content, ...)
    self.manager.save()
    return entry
```

**策略**：未验证（verified=False）抛异常不写——模型口头说「做成了」不算数，只有验证通过的
事实才沉淀为记忆（课件验收「未验证任务不会被 Reflection 写成成功经验」）。

### 4. 反馈闭环（`feedback`）

```python
def feedback(self, task_success: bool) -> None:
    delta = 0.1 if task_success else -0.1
    for entry in 上次注入的记忆:
        entry.confidence = min(1.0, max(0.0, entry.confidence + delta))
    self.manager.save()
```

**策略**：任务结果 → 记忆置信度。成功 → confidence +0.1（正强化），失败 → -0.1（衰减，它们
可能误导了 agent）。因为 confidence 乘进 BM25 评分，所以「成功过的记忆下次更容易被召回，
失败过的记忆下次更靠后」——这是记忆系统的**最外层学习闭环**。

### 5. 维护（`maintain`）

```python
def maintain(self) -> int:
    # 失效 expires_at 已过的记忆，返回失效条数
```

**策略**：一次性事实（如「本机临时改了端口」）设了 `expires_at`，过期自动失效、不再注入。
这是记忆生命周期的收尾——不失效的过期记忆会一直占位、误导。

### 6. 可关闭的高级模块（vector / timeline）

reranker 是可选构造参数（None = 禁用）。vector / timeline 需接真实模型或向量库，超出
「运行时零依赖」底线，Day 20 不实现——它们在课件里也标了「可选 / 专用研究能力」。

### 7. 接入 agent_loop（发请求前注入 + 任务结束反馈）

MemoryPipeline 不是独立玩具，接进 agent_loop 才闭环。两个接入点：

```python
# ① 发请求前：注入项目记忆（在工作记忆注入之后）
context_ratio = usage / DEFAULT_CONTEXT_WINDOW
project_text = project_memory_pipeline.inject(task.goal, context_ratio)
if project_text:
    history = upsert_project_memory(history, project_text)

# ② 任务结束：反馈结果（统一出口 _finish）
def _finish(history, reason):
    project_memory_pipeline.feedback(task_success=(reason == "done"))
    return history, reason
```

**策略**：项目记忆作为 `[Project memory]` system 消息注入，和工作记忆（`[Stable task state]`）
是两条独立的 system 消息、各自 upsert、互不覆盖；压缩时 system 消息原样保留，两者都不进摘要。

**待办（Day 23 重构）**：这两个接入点（发请求前 = AGENT_START、任务结束 = AGENT_STOP）目前
是「直接调」的权宜——hooks 机制是课件 Day 23（skills_hooks）才做，现在还没有 hooks.py。
Day 23 建立 hooks 后，应把这两个点重构成 hook 触发，把 MemoryPipeline 从 loop 里挪出去
（参考项目就是 hooks + orchestrator）。

## 讲解

### 数据流

```
查询 + 上下文压力 ──decide_injection──▶ off/summary/full 策略
                                          │（非 off）
                                          ▼
                            search(BM25) ──▶ rerank(可选，失败降级) ──▶ 截断 ──▶ 格式化
                                          │（cooldown 拦截重复任务）
                                          ▼
                                    注入 system prompt

任务结果 ──feedback──▶ 上次注入记忆的 confidence（成功 +0.1 / 失败 -0.1）

验证通过的事实 ──write_verified_fact──▶ add + save（未验证抛异常）

maintain ──▶ 失效 expires_at 已过的记忆
```

### 关键设计决策

1. **为什么 Loop 只依赖外观接口？** 直接 import 七个记忆模块，任何内部替换都扩大影响面。
   外观把「检索 + 重排 + 注入 + 反馈 + 维护」收进四个动词，内部变化对 Loop 透明。
2. **为什么注入量随压力反向？** 记忆是辅助，上下文是主角。压力大还塞全文，会挤掉当前对话
   的关键信息；所以临界不注入、高压摘要、低压全文。
3. **为什么 rerank 失败要降级而不是报错？** 重排是「锦上添花」，不是「雪中送炭」。BM25 基础
   排序已经可用，rerank 失败应该回退，而不是让注入中断——「可选能力不能成为主链路的单点」。
4. **为什么 cooldown？** 同一个任务反复调用 inject，会反复注入一模一样的一批记忆，浪费 token
   还制造噪声。按 query hash 判断「相同任务」，冷却期内跳过。
5. **为什么 feedback 要闭环？** 记忆的价值在于「帮没帮上忙」。成功过的记忆加强（下次更靠前），
   失败过的记忆衰减（下次更靠后）——这是记忆系统「自我纠偏」的机制。

### 易错点：可选能力不能成为硬依赖

reranker / vector / timeline 都是「可选」，主流程必须能在它们全部禁用时正常工作。如果
「禁用 reranker 就崩」「没装向量库就报错」，说明可选能力泄漏进了主链路——这是 Day 20 验收
「禁用 Reranker/Vector/Timeline 时主流程测试全通过」要防的。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 禁用 Reranker/Vector/Timeline 主流程通过 | ✅ reranker=None 时 inject 照常 |
| Reranker 失败自动回退 BM25 排序 | ✅ try/except 降级 |
| 相同任务冷却期不重复注入 | ✅ cooldown（query hash + 时间戳） |
| 未验证任务不被写成成功经验 | ✅ write_verified_fact verified 校验 |
| Timeline 不污染普通记忆 | ✅ 不实现（专用研究能力，零依赖外） |
| 上下文压力三档注入 | ✅ decide_injection off/summary/full |
| 任务结果闭环调整置信度 | ✅ feedback 成功 +0.1 / 失败 -0.1 |
| 接入 agent_loop（发请求前注入 + 任务结束反馈） | ✅ inject + _finish feedback |
| 既有测试无回归 | ✅ 121 passed |

## 阶段复盘（核心能力阶段收官）

现在系统可以**长时间工作、跨进程恢复，并在下一会话复用已确认知识**。应能清楚区分：

- **Context** 是本次请求；
- **Working Memory** 是当前任务受保护事实；
- **Project Memory** 是跨会话知识；
- **Session** 是完整可恢复记录。

Day 14（turn kernel）到 Day 20（记忆流水线）这一整段「核心能力」至此闭环。接下来 Day 21
起进入「交互界面与扩展生态」——做 CLI / Headless / TUI，**包装已有内核，不再补内核**。
