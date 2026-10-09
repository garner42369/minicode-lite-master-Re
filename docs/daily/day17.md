# Day 17：上下文预算——先减少浪费，再谈智能摘要

## 今日任务

长任务最先爆掉的通常不是推理，而是**重复文件内容、超大命令输出、每轮完整 Prompt**。
今天建立 Token 观测、工具输出预算、重复读取去重和确定性轻量裁剪——**都是不调模型
的确定性省钱**。LLM 摘要压缩留到 Day 18。

## 任务分析

**为什么「先减少浪费，再谈智能摘要」？**

上下文是稀缺资源，能省掉的花费有免费的和付费的两种：

- **免费的**：重复读同一文件、超大工具输出整个塞进上下文、连续重复的进度播报——
  这些用**纯规则**就能去掉，不花一分模型调用；
- **付费的**：把旧历史摘要成一段（LLM 压缩）——又慢又贵又引入模型的不可控性。

正确的顺序是：**先把免费的省掉，再谈付费的智能摘要**。Day 17 做的就是前半段。

## 代码实现

### 1. Token 观测（`context_manager.py`）

```python
# 模型上下文窗口表（可更新配置）；未知模型用保守默认值
CONTEXT_WINDOWS = {"deepseek-v4-pro": 1_000_000, "deepseek-v4-flash": 1_000_000}
DEFAULT_CONTEXT_WINDOW = 128_000
_USAGE_THRESHOLDS = (0.5, 0.75, 0.9)   # 使用率阈值


@dataclass(frozen=True, slots=True)
class ContextStats:
    estimated_tokens: int
    context_window: int
    reserved_output: int
    source: str = "estimated"          # 来源标记：estimated / provider

    @property
    def usable_window(self) -> int:
        return max(1, self.context_window - self.reserved_output)

    @property
    def usage_ratio(self) -> float:
        return self.estimated_tokens / self.usable_window


def estimate_tokens(text: str) -> int:
    ascii_chars = sum(ord(ch) < 128 for ch in text)
    cjk_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + cjk_chars)   # ASCII 4字符/token，CJK 1字/token


def threshold_events(stats: ContextStats, fired: set[float]) -> list[str]:
    events = []
    for t in _USAGE_THRESHOLDS:
        if stats.usage_ratio >= t and t not in fired:
            fired.add(t)
            events.append(f"context usage {t:.0%} ({stats.estimated_tokens}/{stats.usable_window})")
    return events
```

**策略**：`usage_ratio` 的分母是「窗口 - 预留输出」，不是整个窗口——因为要给模型
的**回复**留空间，不能把窗口全塞给历史。`threshold_events` 用 `fired` 集合去重，
同一阈值只触发一次，避免每轮都刷屏。

### 2. 大结果存磁盘（`ToolResultBudgetManager`）

```python
class ToolResultBudgetManager:
    def __init__(self, artifact_dir: Path, limit: int = 2_000) -> None:
        self.artifact_dir, self.limit = artifact_dir, limit

    def process(self, output: str) -> tuple[str, dict]:
        if len(output) <= self.limit:
            return output, {}                     # 没超限：原样返回

        content_hash = hashlib.sha256(output.encode()).hexdigest()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = self.artifact_dir / f"{content_hash}.txt"
        artifact.write_text(output, encoding="utf-8")   # 完整内容落盘

        half = self.limit // 2
        summary = (output[:half]
                   + f"\n...[truncated: {len(output)} chars, full output at {artifact}]...\n"
                   + output[-half:])
        meta = {"truncated": True, "original_size": len(output), "artifact": str(artifact)}
        return summary, meta
```

**策略**：超大输出不是「丢弃」，而是**完整内容写进 artifact 文件，模型只收 head +
tail + 省略标记**。省略标记明确带 artifact 路径和原始大小，模型和用户都知道「完整
内容在哪、有多大」，不会误以为「只有这点」。

### 3. 读取去重（`ReadDedupManager`）

```python
class ReadDedupManager:
    def __init__(self) -> None:
        self._reads: dict[str, str] = {}   # path -> 上次的 content hash

    def process(self, path: str, content: str) -> tuple[str, bool]:
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        previous = self._reads.get(path)
        if previous == content_hash:
            return f"[unchanged duplicate read: {path}, hash={content_hash[:8]}]", True
        self._reads[path] = content_hash
        return content, False
```

**策略**：按 `path + content hash` 去重。相同文件、内容没变 → 返回 stub（省掉整段
重复内容）；内容变了（hash 不同）→ 返回新内容并更新记录。这样「重复读不占上下文」，
但「文件改后再读」能拿到最新内容，不会被旧 stub 拦下。

### 4. 确定性轻量裁剪（`micro_compact.py`）

```python
def micro_compact(messages: list[dict]) -> list[dict]:
    compacted = []
    for msg in messages:
        if compacted and compacted[-1] == msg:      # 连续重复 → 去重
            continue
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("assistant", "assistant_progress") and isinstance(content, str) and not content.strip():
            continue                                # 空 progress/assistant → 去空
        compacted.append(msg)
    return compacted
```

**策略**：只做**确定性**裁剪（连续重复去重、空消息去空），不调用模型。纯函数、可
预测、可测试。

### 5. 受保护工作记忆（`working_memory.py`）

```python
@dataclass(frozen=True, slots=True)
class WorkingMemoryEntry:
    key: str
    content: str
    importance: int = 1         # 重要度，越高越难淘汰
    ttl: float = float("inf")   # 存活秒数，inf = 永不过期
    created_at: float = 0.0


class WorkingMemory:
    def set(self, key, content, *, importance=1, ttl=float("inf")): ...
    def protected_facts(self) -> list[str]:          # 按重要度降序返回
        return [e.content for e in sorted(self._entries.values(), key=lambda e: -e.importance)]
    def evict_expired(self) -> list[str]:            # 淘汰 TTL 过期的条目
        ...
```

**策略**：保存「必须保留的事实」（任务目标、关键约束），供 Stable Task Pack 注入；
按重要度 + TTL 淘汰，不能无限增长。Day 17 只定义接口，完整淘汰策略 Day 18 完善。

## 讲解

### 每个模块为什么存在

| 模块 | 减少的浪费 | 目的 |
| --- | --- | --- |
| `ContextStats` | ——（观测） | 知道「用了多少、还剩多少」，触发阈值提醒 |
| `ToolResultBudgetManager` | 超大工具输出 | 完整内容存磁盘，模型只收摘要 |
| `ReadDedupManager` | 重复读同一文件 | 相同内容只进一次上下文 |
| `micro_compact` | 连续重复/空消息 | 确定性裁剪历史 |
| `WorkingMemory` | 关键事实无限增长 | 受保护 + 按重要度/TTL 淘汰 |

### 数据流

```
工具输出 ──ToolResultBudgetManager──▶ 超限则「摘要 + artifact」，未超限原样
读取文件 ──ReadDedupManager──────────▶ 相同 hash 则 stub，否则原内容
消息历史 ──micro_compact─────────────▶ 去掉重复/空消息
当前 token ──ContextStats────────────▶ usage_ratio → 阈值事件
关键事实 ──WorkingMemory─────────────▶ 受保护、按重要度/TTL 淘汰
```

### 集成进 agent_loop

模块本身「独立可测」，但只有接进 `agent_loop` 写 `tool_result` 的地方，才真正做到
「模型只收到摘要版」。集成点在工具结果回填处（`agent_loop.py`）：

```python
# 写 tool_result 前：先读取去重，再大结果落盘
output = result.output
if call.name == "read_file":
    output, _ = read_dedup.process(str(call.arguments.get("path", "")), output)
output, budget_meta = tool_budget.process(output)
```

- `ToolResultBudgetManager` 的 artifact 目录 = `<cwd>/.mini-code-tool-results`（对齐参考项目命名）；
- `ReadDedupManager` 只对 `read_file` 去重（重复读返回 stub，文件变化自动失效）；
- 落盘时 `tool_result` 消息加 `artifact` 字段。

### 关键设计决策

1. **为什么大结果「存磁盘」而不是「直接截断丢弃」？** 直接截断会**丢信息**——日志
   的最后几行往往是错误汇总，只留头部就丢了它。存磁盘保证完整内容可追查，模型收到
   的摘要是「可追踪的」（带 artifact 路径 + 原始大小），不是「被销毁的」。
2. **为什么读取去重要「文件变化失效」？** 去重是为了省「重复读同一内容」；但若只按
   path 去重，文件改后再读也会被旧 stub 拦截，导致模型用**陈旧内容**决策。按
   `path + hash` 去重，hash 变了就自动失效。
3. **为什么 Token 要标记 `estimated`？** Provider 的 usage 是「已完成请求的事实」，
   但下一次请求的 token 只能估算（字符数公式是近似）。诚实标记来源，UI 显示
   estimated，不拿字符数冒充精确计费。
4. **为什么 micro_compact 不调模型？** 去重复、去空是纯规则就能做的，用模型是
   杀鸡用牛刀。确定性裁剪可预测、可测试、零成本。

### 易错点：不同工具不同截断策略

只留头部会丢错误汇总，只留尾部可能丢命令和环境。通用策略至少保留 head、tail、
错误行和规模信息——Day 17 先做 head/tail + 规模，错误行保留后续按工具细化。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 同一内容重复读不重复占满上下文 | ✅ `ReadDedupManager` 返回 dedup stub |
| 文件修改后再读获得新内容 | ✅ hash 变化自动失效，返回新内容 |
| 大结果 artifact 可查看、摘要标注截断 | ✅ 写 artifact + meta(truncated/original_size/artifact) |
| 上下文达 50/75/90% 产生一次阈值事件 | ✅ `threshold_events` 用 fired 去重，不刷屏 |
| 既有测试无回归 | ✅ 94 passed（本章新增 14 个测试） |
