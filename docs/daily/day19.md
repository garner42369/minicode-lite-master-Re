# Day 19：项目记忆——把跨会话知识变成可管理的数据

## 今日任务

上下文解决「当前会话」，工作记忆解决「当前任务」，今天解决「**下次还需要知道什么**」——建立
跨会话、跨进程的**项目记忆**：user/project/local 三种 scope、BM25 检索和显式管理。

## 任务分析

### 为什么 Day 19 是重点

Day 17（上下文预算）和 Day 18（压缩 + 工作记忆）解决的都是「**一次运行内**」的问题：历史太长
怎么省、任务状态怎么不丢。但进程一结束，这些全没了。Day 19 是记忆体系的最后一块拼图——它让
系统**跨会话、跨进程**记住「测试命令、目录约定、已确认的架构决定、用户代码风格」，是 Day 20
记忆流水线（injector / reranker / vector）的地基。参考项目 `memory.py` 高达 1986 行，是核心大
模块。

### 三层记忆的边界

| 层次 | 存什么 | 生命周期 | 对应 Day |
| --- | --- | --- | --- |
| Context（上下文） | 本次请求的对话 | 一次请求 | Day 17 |
| Working Memory（工作记忆） | 当前任务目标/约束/验证状态 | 一个任务 | Day 18 |
| Project Memory（项目记忆） | 跨会话可复用知识 | 跨会话、跨进程 | **Day 19** |

三者的数据流关系：

```
用户请求 ──▶ Context（对话历史）
                │ 压缩时保护
                ▼
          Working Memory（任务状态，StableTaskPack 快照，每轮重建）
                │ 跨会话沉淀
                ▼
          Project Memory（可复用知识，落盘，跨进程召回）
```

Day 18 的 Working Memory 是「当前任务」的保护事实，随任务结束消失；Day 19 的 Project Memory
是「跨任务」的知识，落盘持久化。两者一个解决「这次别忘」，一个解决「下次还想得起来」。

### 三条红线（课件易错点，决定了记忆系统的边界）

1. **记忆 ≠ 聊天归档**：适合长期记忆的是「测试命令、目录约定、已确认的架构决定、用户代码
   风格」；**不能自动记忆**：密钥（安全风险）、一次性错误（噪声）、模型未验证的猜测（不可信）、
   可从当前源码快速重读的全文（冗余）。这个「选择性」是项目记忆和聊天日志的本质区别。
2. **scope 隔离比检索算法更重要**：不同工作区不能因关键词相同而互相注入私有事实。检索算法只
   决定「找得准不准」，scope 隔离决定「该不该找」——后者是隐私和正确性底线。
3. **搜索命中 ≠ 内容仍然真实**：记忆是「过去某时刻的事实」，可能已被当前代码推翻。当前用户
   明确指令和重新读取的项目事实优先级高于旧记忆；冲突记忆应降 confidence 或进待确认状态。

## 代码实现

### 1. 三 scope 隔离（`MemoryScope` + `MemoryPaths`）

```python
class MemoryScope(str, Enum):
    USER = "user"        # 跨项目，~/.mini-code/memory/
    PROJECT = "project"  # 项目共享，<workspace>/.mini-code-memory/
    LOCAL = "local"      # 项目本地，<workspace>/.mini-code-memory-local/


class MemoryPaths:
    @classmethod
    def for_workspace(cls, workspace):
        workspace_path = Path(workspace).resolve()
        return cls(
            user_memory=Path.home() / ".mini-code" / "memory",
            project_memory=workspace_path / ".mini-code-memory",
            local_memory=workspace_path / ".mini-code-memory-local",
        )

    def for_scope(self, scope):
        return {USER: ..., PROJECT: ..., LOCAL: ...}[scope]
```

**策略**：user 是全局的（所有 workspace 共享），project/local 绑定 workspace 路径。**路径隔离
是最硬的一道闸**——不同 workspace 的 project/local 落在不同目录，物理上就不可能互相注入，
不必靠关键词或内容判断。

**为什么三个 scope 而非一个**：一个项目里，「我的个人偏好」（user，跨项目）、「团队约定的
架构」（project，项目共享）、「本机临时调试命令」（local，仅本机）是三种不同粒度的知识，混在
一起既难管理、也难控制「谁能看到什么」。

### 2. 记忆条目（`MemoryEntry`）

```python
@dataclass
class MemoryEntry:
    id: str
    content: str
    scope: MemoryScope
    category: str = "general"           # architecture / convention / decision / pattern...
    tags: list[str] = field(default_factory=list)
    source: str = ""                     # 来源（哪个文件/命令/决策）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    confidence: float = 1.0              # 置信度，冲突时降
    valid: bool = True                   # 有效性，失效后不注入
    expires_at: float | None = None      # 过期时间戳，None = 永不过期
```

**字段语义（课件最少字段 + 生命周期）**：
- `source`：记住「这条记忆从哪来」，否则无法判断「它现在还成立吗」；
- `confidence`：冲突记忆降置信度，检索时乘到评分上，让「不确定的记忆」排后面；
- `valid`：软删除，失效后不注入、不参与检索，但保留记录；
- `expires_at`：一次性事实（如「本机临时改了端口」）过期自动失效。

### 3. 原子写 + 损坏恢复

```python
def save_memory_file(path, entries):
    # mkstemp + os.replace 原子写：写一半崩溃不留「半截 JSON」被当成有效记忆

def load_memory_file(path):
    # 文件不存在 → 空列表；JSON 损坏 → 抛可诊断 ValueError（不静默清空）

def _recover_entries(entries):
    # 去无效 ID、去重 ID、修空 category（补 general）、去空 content
```

**策略**：两级容错——
- **文件级**：JSON 解析失败抛 ValueError，让调用方知道「文件坏了」，而不是悄悄清空（清空会
  把用户积累的记忆全丢）；
- **条目级**：文件能解析但个别条目损坏（重复 ID、空 content、空 category），自动恢复：重复 ID
  保留第一个、空 content 移除、空 category 补成 "general"。恢复后立即落盘修复，避免每次载入
  都重复恢复。

### 4. CRUD + 敏感信息拒绝

```python
class MemoryManager:
    def add(self, scope, content, *, category, tags, source, confidence): ...
    def update(self, scope, entry_id, content): ...
    def delete(self, scope, entry_id): ...
    def list(self, scope): ...
    def add_tag / remove_tag(self, scope, entry_id, tag): ...

    @staticmethod
    def _reject_secret(content):
        if _SECRET_PATTERN.search(content):
            raise ValueError("refusing to store possible secret")
```

**策略**：add/update 前先过敏感信息正则 `(api[_ -]?key|password|secret|token)\s*[:=]`，命中
就拒绝落盘——「敏感模式内容不自动落盘」是硬红线，靠正则拦截在**写入前**，而不是靠「注入时
过滤」这种事后补救。

### 5. 标准 Okapi BM25 检索

分词（英文单词 + CJK 单字，中文无空格所以单字是基本匹配单位）：

```python
def _tokenize(text):
    tokens = [w.lower() for w in re.findall(r"[a-zA-Z0-9_]+", text)]
    tokens.extend(re.findall(r"[一-鿿]", text))   # CJK 单字
    return tokens
```

经典 BM25 IDF + 评分：

```python
def _compute_idf(documents):
    # 经典 BM25 IDF：log(1 + (N - df + 0.5) / (df + 0.5))
    return {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()}


def _bm25_score(query_tokens, doc_tokens, idf, avgdl, k1=1.5, b=0.75):
    doc_len = len(doc_tokens)
    score = 0.0
    for term in set(query_tokens):
        tf = doc_tokens.count(term)   # 原始词频，不归一化
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * (doc_len / avgdl))
        score += idf[term] * (numerator / denominator)
    return score
```

**踩坑修复（重点）**：参考项目 `memory.py` 的 BM25 实现有 bug——它的 `_compute_tf` 返回
`count/total`（归一化词频），`_compute_idf` 用 `log((n+1)/(df+1))+1`（sklearn TF-IDF 的平滑
IDF）。后果是：

1. **tf 除以 doc_len 把文档长度算了两次**：标准 BM25 的长度归一化已经由分母的
   `b * (|d|/avgdl)` 完成，tf 再除一次，长文档被系统性过度惩罚；
2. **IDF 是 TF-IDF 风格而非 BM25 风格**：缺少经典 BM25 的 0.5 平滑项。

所以那段是「BM25 外壳 + TF-IDF 内胆」的混杂实现。这里修成**标准 Okapi BM25**：tf 用原始词频
+ 经典 BM25 IDF（`log(1 + (n - df + 0.5) / (df + 0.5))`）。这正是 CLAUDE.md 说的「把坑踩一遍
再修复」——参考项目的坑，不照抄，修对。

### 6. 检索 + 注入（`search` + `get_relevant_context`）

```python
def search(self, query, scope=None, limit=5):
    # BM25 评分 × confidence，加子串命中加分，排序 + 去重 + top N

def get_relevant_context(self, query=None, max_entries=20, max_tokens=8000):
    # 有 query → BM25 检索；无 query → 全部记忆
    # 优先级 LOCAL > PROJECT > USER（越局部越优先）
    # 逐条加入，token 超预算（max_tokens）就停
    # format 成 markdown，返回要注入 system prompt 的文本
```

**策略**：`search` 是「找」，`get_relevant_context` 是「注入」——两者是记忆系统的出口。
`get_relevant_context` 内部做三件事：检索（有 query 时 BM25，无 query 时全部）→ 预算限制
（条数 + token，逐条加、超了停）→ markdown 格式化。删除某条记忆后，这里自然不再返回它
（课件验收「删除后不再注入」）。

## 讲解

### 每个模块为什么存在

| 模块 | 回答的问题 | 核心职责 |
| --- | --- | --- |
| `MemoryScope` | 这条知识属于谁 | 三 scope 隔离（隐私/正确性底线） |
| `MemoryEntry` | 一条记忆长什么样 | 内容 + 来源 + 置信度 + 有效期 |
| `MemoryPaths` | 记忆存在哪 | 路径隔离（user 全局 / project+local 绑 workspace） |
| 原子写/读 | 怎么可靠落盘 | mkstemp+replace 不留半截、损坏可诊断 |
| `_recover_entries` | 坏了怎么救 | 去无效/去重/修空，恢复后落盘 |
| `_reject_secret` | 什么不能记 | 密钥/密码/secret/token 拒绝落盘 |
| `_tokenize` + BM25 | 怎么找得准 | 标准 Okapi BM25 相关性排序 |
| `get_relevant_context` | 怎么注入 | 检索 + 预算 + markdown 格式化 |

### 数据流

```
写入：add/update/delete ──▶ MemoryManager（内存，敏感拒绝 + CRUD）
                          └─ save ──▶ 原子落盘 <workspace>/.mini-code-memory/memory.json

载入：load_memory_file ──▶ _recover_entries ──▶ 损坏条目自动恢复 + 落盘修复

检索：query ── _tokenize ──▶ search(BM25 × confidence) ──▶ 排序 + 去重 + top N

注入：get_relevant_context ──▶ 检索/全部 ──▶ 预算限制 ──▶ markdown ──▶ system prompt
```

### 关键设计决策

1. **为什么 scope 隔离比检索算法更重要？** 检索算法只决定「找得准不准」，scope 隔离决定「该
   不该找」。两个工作区的私有事实若因关键词相同而互相注入，是隐私泄露 + 决策污染。路径隔离
   是最硬的一道闸——不同 workspace 的 project/local 物理上就在不同目录。

2. **为什么记忆 ≠ 聊天归档？** 聊天归档「什么都记」，项目记忆要「选择性」：只记可复用、跨会话
   有用的知识。密钥（安全风险）、一次性错误（噪声）、未验证猜测（不可信）、可重读全文（冗余）
   都不该自动记。这个选择性是项目记忆和日志的本质区别。

3. **为什么 BM25 的 tf 不除以 doc_len？** 标准 BM25 的长度归一化已经由分母的 `b * (|d|/avgdl)`
   完成，tf 再除一次就把长度影响算了两次，长文档被系统性压低。这是从参考项目踩到的坑，修成
   标准 Okapi BM25。

4. **为什么注入要限制条数和 token？** 记忆注入是每轮请求都做的，不设预算会无限膨胀、挤占对话
   上下文。逐条加入直到超预算，保证「记忆是辅助，不喧宾夺主」。

5. **为什么 confidence/valid/expires 都要有？** 记忆会过时、会冲突、会失效。`confidence` 处理
   「可信度」（冲突时降），`valid` 处理「有效性」（软删除），`expires_at` 处理「时效」（一次性
   事实过期自动失效）。三者是记忆生命周期的三根支柱。

### 易错点：搜索命中 ≠ 内容仍然真实

记忆是「过去某时刻的事实」，可能已被当前代码推翻。所以当前用户明确指令和重新读取的项目事实
优先级高于旧记忆；冲突记忆应降 confidence 或进待确认状态，而不是盲目信任检索结果。这也是
`confidence` 字段存在的意义——它不是装饰，是「旧记忆可能错」的诚实表达。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 跨会话召回 | ✅ save → 重新 load，记忆保留 |
| 跨项目隔离 | ✅ 不同 workspace 的 project 记忆互不可见 |
| 删除/失效立即生效 | ✅ delete 后 get_relevant_context 不再注入 |
| 记忆损坏有恢复、不静默清空 | ✅ JSON 损坏抛异常；条目损坏自动恢复 |
| 敏感模式不自动落盘 | ✅ api_key/password/secret/token 拒绝 |
| BM25 检索排序正确 | ✅ search 最相关的排第一（标准 Okapi BM25） |
| 注入限制条数/token | ✅ get_relevant_context 逐条加、超预算停 |
| 既有测试无回归 | ✅ 113 passed |
