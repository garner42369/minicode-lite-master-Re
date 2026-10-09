# Day 15：Session 持久化——保存的是运行状态，不只是聊天文本

## 今日任务

进程结束后，Agent 需要知道之前做过什么、改过哪些文件、验证是否有效。今天实现可版本化、
可原子保存的会话，把「运行状态」落盘，而不是只存聊天文本。

## 任务分析

前 14 天 Agent 跑完一轮，进程结束，所有状态（消息、工具结果、checkpoint、验证证据）都随
进程消失。Day 15 引入 Session，把「重建运行时所需的数据」持久化，让下一次进程能知道「之前
发生了什么」。

## 代码实现

### 1. SessionData —— 可版本化的会话数据

```python
class SessionData:
    session_id: str
    workspace: str                       # workspace identity：resolve 后的规范绝对路径
    created_at: float
    updated_at: float
    messages: list = []                  # 继续模型对话
    events: list = []                    # 人类检查发生过什么
    checkpoints: list = []               # 预览/回退文件
    task_state: dict = {}                # 完成/暂停/失败
    verification_evidence: dict = {}     # 验证是否仍有效
    runtime_snapshot: dict = {}          # 脱敏环境快照
    cost_usage: dict = {}                # token/成本
    schema_version: int = SCHEMA_VERSION
```

**关键点**：所有字段都是「可 JSON 序列化的纯数据」——不 pickle 运行时对象（模型客户端、线程、
回调都不可安全恢复）。

### 2. save_session / load_session —— 原子保存 + 版本校验

```python
def save_session(session, directory):
    directory.mkdir(parents=True, exist_ok=True)
    session.updated_at = time.time()
    target = directory / f"{session.session_id}.json"
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")   # 先写临时文件
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(session), handle, ensure_ascii=False, indent=2)
    os.replace(temporary, target)       # 再原子替换（不留半截 JSON）
    return target


def load_session(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported session schema: {data.get('schema_version')}")   # 版本校验
    known = {f.name for f in fields(SessionData)}
    return SessionData(**{k: v for k, v in data.items() if k in known})   # 多余字段容错忽略
```

**关键点**：

- `save_session` 用 `mkstemp + os.replace` 原子写——写一半崩溃也不会留下「半截 JSON」被当成
  有效会话；
- `load_session` 校验 `schema_version`（旧版本拒绝），多余字段容错忽略（schema 演进），缺字段
  用默认值。

## 讲解

### 每个模块为什么存在

| 模块 | 职责 | 与 Session 的关系 |
| --- | --- | --- |
| `session.py` | SessionData + 原子保存/载入 + 管理操作 + Autosave/Delta | 核心 |
| `history.py` | 用户输入历史（上下键复用） | 只存用户输入，不替代 Session |
| `state.py` | 进程内可观察状态（State Store） | 存「当前」，不取代持久 Session |

### 数据流

```
Agent 跑完一轮（有 messages / checkpoints / verification_evidence / cost）
        │
        ▼
save_session(session, directory)
        ├── 原子写（临时文件 + replace，不留半截）
        └── 落盘为 <session_id>.json
        │
        ▼（下次进程）
load_session(path)
        ├── 校验 schema_version
        └── 载入 SessionData → 供 Day 16 的 replay/resume/rewind 使用
```

### 关键设计决策

1. **为什么不 pickle 运行时对象？** 模型客户端、线程、进程、回调都不可安全恢复——Session 保存
   的是「重建运行时所需的数据」，不是「Python 对象快照」。pickle 会把不可序列化的运行时引用
   一起存进去，恢复时直接崩。

2. **为什么需要 schema version？** Session 结构会随 Day 演进（加字段、改结构），带
   `schema_version` 载入时校验、不匹配拒绝。旧文件如果被静默当成新结构载入，会得到错乱的
   数据；显式的版本号让「旧文件」能被**可诊断地拒绝**。

3. **为什么原子写（mkstemp + os.replace）？** 直接写目标文件，进程在写一半时崩溃，会留下
   「半截 JSON」——下次载入把它当有效会话，导致更糟的错乱。原子写保证「要么旧文件、要么完整
   新文件」，不存在中间态。

4. **为什么 history 和 StateStore 要和 Session 分开？** Session 是「完整、持久、可恢复」的权威
   状态；用户输入历史是「轻量、频繁、只读」的交互辅助；StateStore 是「当前」的临时状态（进程
   结束就消失）。三者职责边界清晰，混在一起会让 Session 被污染。

## Session 在 Agent Runtime 里的位置

Session 是「**持久化**」层，是 Day 16 恢复能力的直接地基：

- **保存的是「重建运行时所需的数据」**：messages（继续对话）、checkpoints（回退文件）、
  verification_evidence（判断验证是否仍有效）、cost_usage（审计成本）——每一项都有明确的恢复
  用途；
- **Day 16 直接复用**：replay 读 `session.events` 回放、resume 用 `session.messages` 继续、
  rewind 用 `session.checkpoints` 回退文件——持久化是「恢复」的地基；
- **workspace identity 防串用**：`workspace` 存 resolve 后的规范路径，不同 workspace 的 session
  不会串用——这是「恢复」时「找对会话」的前提。

所以 Day 15 的价值不只是「能存」，更是确立了「**运行状态可以被可靠地落盘、再可靠地读回**」
这个能力——这是 Day 16「恢复」的前置条件。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 保存中断不留半截 JSON | ✅ 原子写（mkstemp + os.replace） |
| 无变化时 Delta save 幂等 | ✅ `AutosaveManager.force_save` 无新增不写盘 |
| None / 旧 schema / 损坏文件可诊断 | ✅ `load_session` 分别抛可诊断 ValueError |
| 不同 workspace 的 session 不串用 | ✅ workspace 存 resolve 规范路径 |
| Session 文件不含 API Key | ✅ runtime_snapshot 只存脱敏数据 |
| 既有测试无回归 | ✅ 80 passed |

## 小结

Day 15 的核心是「保存的是运行状态，不只是聊天文本」。前 14 天 Agent 跑完一轮，进程结束，所有
状态随进程消失；今天用 Session 把「重建运行时所需的数据」落盘。这一天确立了几个「持久化」的
通用原则：**不 pickle 运行时对象**（只存可序列化的纯数据）、**schema version 管版本兼容**、
**原子写保证不留半截文件**。而 history（用户输入历史）和 StateStore（进程内状态）和 Session
的严格区分，是为了守住「Session 是权威的持久状态」这个定位。这套 Session 会被 Day 16 直接
复用——replay 读事件、resume 重建状态、rewind 用 checkpoint 回退文件，持久化是「恢复」的地基。
