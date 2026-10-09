# Day 16：Replay、Resume 与 Rewind——恢复必须避免重复副作用

## 今日任务

Day 15 能保存会话了，但「保存」只是把状态落盘。今天让用户能**只读回放（replay）、
继续未完成任务（resume）、预览并安全撤回文件改动（rewind）**。

## 任务分析

**为什么三种「恢复」操作必须分开？** 它们看起来相似，但「调不调模型」「执不执行
工具」完全不同，混在一起会出严重问题：

| 操作 | 调模型 | 执行工具 | 目的 |
| --- | --- | --- | --- |
| replay | 否 | 否 | 只读展示历史事实 |
| resume | 是 | 可能 | 以已保存状态继续 |
| rewind | 否 | 只恢复受管文件 | 撤回 edit group |

- 把 **replay 当 resume** → 会误调模型，白花 token 还可能有副作用；
- 把 **rewind 当 resume** → 会误执行工具；
- 把 **resume 当 replay** → 会漏掉「继续任务」该做的事。

所以 Day 16 的核心是把三者的可信边界钉死：replay 绝无副作用，rewind 只碰受管
文件，只有 resume 才可能调模型。

## 代码实现

### 1. rewind 核心（`file_review.py`）—— 预览 + 冲突检测 + 原子回退

复用 Day 12 的 `Checkpoint`（它有 `path/existed/before/before_hash/after_hash/group_id`
全套字段），不新造结构。核心是两个函数：

```python
def preview_rewind(checkpoints: list[Checkpoint]) -> list[str]:
    """只读预览回退动作：展示每个文件将被「恢复」还是「删除」。"""
    return [
        f"{'restore' if cp.existed else 'delete'} {cp.path} [{cp.group_id}]"
        for cp in checkpoints
    ]


def rewind_group(checkpoints: list[Checkpoint]) -> None:
    """按 edit group 原子回退：先预检全部文件，任一冲突则整个组不写。"""
    # 1. 预检：全部文件的当前 hash 必须等于 after_hash
    conflicts: list[str] = []
    for cp in checkpoints:
        target = Path(cp.path)
        current = target.read_bytes() if target.exists() else b""
        if digest(current) != cp.after_hash:
            conflicts.append(cp.path)
    if conflicts:
        raise RuntimeError("rewind conflicts: " + ", ".join(conflicts))

    # 2. 恢复（预检全部通过才执行，保证原子性；恢复也用原子写）
    for cp in checkpoints:
        target = Path(cp.path)
        if cp.existed:
            atomic_write(target, cp.before)   # 已有文件 → 恢复 before
        else:
            target.unlink(missing_ok=True)    # 新建文件 → 删除
```

**执行策略的关键点**：

- `digest(current) != cp.after_hash` 是冲突判定：`after_hash` 是 Agent 写入后的
  内容哈希，回退前读当前文件重新哈希，两者不等说明「Agent 写完后、用户回退前，
  文件又被改过」，此时**绝不能用 before 覆盖**。
- 预检是**两遍循环**：第一遍只检查、收集所有冲突，第二遍才真正写。这样保证
  「要么全回、要么全不回」，不会回退到一半才发现第二个文件冲突。

### 2. 恢复记录（`RewindRecord`）—— 可审计

```python
@dataclass(frozen=True, slots=True)
class RewindRecord:
    timestamp: float
    actions: tuple[tuple[str, str], ...]  # (动作, 路径)：restore / delete


def build_rewind_record(checkpoints: list[Checkpoint]) -> RewindRecord:
    return RewindRecord(
        timestamp=time.time(),
        actions=tuple(
            ("restore" if cp.existed else "delete", cp.path) for cp in checkpoints
        ),
    )
```

`RewindRecord` 只记录「回退动作」（哪些文件 restore / delete），**不含 after 内容**，
因此它支持审计（能查到什么时候回了哪些文件），但不支持「撤销回退」——撤销需要
after 内容，留作后续。

### 3. replay（`session.py`）—— 纯只读格式化

```python
def format_session_replay(session: SessionData) -> str:
    """只读回放：格式化 session 的事件历史。"""
    if not session.events:
        return "(no events)"
    return "\n".join(
        f"{event.get('type', '?')}: {event.get('summary', '')}"
        for event in session.events
    )
```

它只读内存中的 `session.events`，**不调模型、不执行工具、不读文件**。所以 replay
前后工作区 hash 必然一致，也不会产生新进程。

### 4. resume 副作用状态（`session.py`）—— 结果未知不自动重放

```python
class SideEffectStatus(str, Enum):
    PENDING = "pending"      # 待执行（还没执行，无副作用）
    RUNNING = "running"      # 已启动、结果未知
    SUCCEEDED = "succeeded"  # 成功，结果已确认
    FAILED = "failed"        # 失败，结果已确认
    UNKNOWN = "unknown"      # 结果未知（崩溃，无法确认）


def needs_verification(status: SideEffectStatus) -> bool:
    return status in {SideEffectStatus.RUNNING, SideEffectStatus.UNKNOWN}
```

## 讲解

### 每个模块为什么存在

| 能力 | 位置 | 谁消费 |
| --- | --- | --- |
| `preview_rewind` / `rewind_group` | file_review.py | resume/rewind 流程 |
| `RewindRecord` / `build_rewind_record` | file_review.py | 审计 |
| `format_session_replay` | session.py | replay 命令 |
| `SideEffectStatus` / `needs_verification` | session.py | resume 流程 |

### 数据流

```
replay：  session.events ──format_session_replay──▶ 人类可读文本（无副作用）
rewind：  checkpoints ──预检(当前hash==after_hash?)──▶ 冲突则抛错 / 否则恢复 before
resume：  session.tool_calls ──needs_verification──▶ running/unknown 先核实，再继续
```

### 关键设计决策

1. **为什么冲突检测用 `after_hash` 而不是直接恢复？** 回退只该撤回「Agent 的修改」。
   如果 Agent 写完后、用户回退前，用户自己又改了这个文件，直接 `write(before)` 会
   **静默覆盖用户的后续编辑**。`after_hash` 是「Agent 写入后的快照指纹」，当前内容
   对不上就说明中间有人动过，必须停下来报告冲突。

2. **为什么预检和恢复分两遍？** 一遍循环里「边检查边写」会在第二个文件冲突时留下
   半回退状态（第一个已回、第二个没回）。两遍循环先收集全部冲突、确认无冲突才写，
   保证 edit group 的原子性。

3. **为什么 `RewindRecord` 不含 after 内容？** 审计只需要「回了什么、什么时候回」。
   「撤销回退」要额外保存 after 内容（回退前的状态），属于另一个能力，Day 16 先不
   做，避免把审计记录做成「完整的反向快照」。

4. **为什么 resume 的 running/unknown 要核实？** 进程可能在「命令已启动、结果尚未
   保存」时崩溃。此时重放会**重复执行副作用**（再发一次网络请求、再删一次文件）。
   所以 `needs_verification(running/unknown) == True`，先让用户/运行时核实。

### 易错点：回退成功 ≠ 系统完全恢复

回退只能恢复「受管文件」（checkpoint 覆盖的文件）。依赖安装、数据库写入、网络调用
这些副作用不在覆盖范围。UI 和报告要明确「恢复了哪些文件、哪些副作用不在覆盖范围」，
不能让人误以为「一键回退 = 完全回到修改前」。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| Replay 前后工作区 hash 一致、无新进程 | ✅ replay 纯格式化，不读文件不调模型 |
| Resume 不自动重复 unknown 命令 | ✅ `needs_verification(running/unknown)=True` |
| 用户后续编辑不被 Rewind 静默覆盖 | ✅ rewind_demo 冲突检测：保持 `user edited later` |
| edit group 不发生部分恢复 | ✅ `rewind_group` 预检全部，任一冲突整组不写 |
| 回退留下审计记录 | ✅ `build_rewind_record` 输出 restore/delete 动作 |
| 既有测试无回归 | ✅ 80 passed |

### 8. 为什么 preview_rewind 是独立的只读函数

`preview_rewind` 和 `rewind_group` 分成两个函数：一个只预览「将恢复/删除哪些文件」，一个才
真正执行回退。

**WHY**：回退是破坏性操作，执行前必须先让用户「看清楚要发生什么」。preview 是「只读的
预演」，它只描述动作、不碰文件，用户看了觉得不对可以取消。如果「预览」和「执行」混在
一个函数里，就无法做到「先看后做」。这是所有破坏性操作的标准姿势：**先给只读的预览，再
给可确认的执行**——和 Day 12 的「先 diff 审批、后写入」是同一个原则。

## 小结

Day 16 的核心是「恢复必须避免重复副作用」。Day 15 能保存会话了，但「保存」只是把状态
落盘，用户还需要三种「恢复」操作——而它们必须分开：replay（只读回放，不调模型不碰工具）、
resume（以保存状态继续，可能调模型）、rewind（撤回文件改动，只碰受管文件）。

这一天的安全设计集中在两个字：**预检**。rewind 回退前，先检查「当前文件 hash 是否还等于
Agent 写入后的 after_hash」——不等就说明用户后来改过，绝不能覆盖；多个文件回退前，先预检
全部，任一冲突就整组不写，绝不留「半回退」。resume 对 running/unknown 的副作用先核实，
绝不自动重放「结果未知」的命令。这些「预检」的共同目标，是让「恢复」这个本来危险的操作，
变得「宁可停下来报告，也不造成二次伤害」。

这套 rewind 直接复用了 Day 12 的 Checkpoint（before/after_hash/existed/group_id）——Day 12
写文件时顺手存的「修改前快照」，正是 Day 16 回退时需要的「恢复凭据」。这也是「Checkpoint
只能恢复文件、不能撤销 shell/网络/数据库副作用」这条诚实边界的由来：回退只对受管文件
有效，其他副作用不在覆盖范围。
