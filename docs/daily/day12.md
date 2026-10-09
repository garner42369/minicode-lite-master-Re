# Day 12：受控写入——Diff、精确编辑与 Checkpoint 是一个原子流程

## 今日任务

今天第一次允许 Agent 改文件。目标不是「能写」，而是修改前可审查、修改后可追踪、出问题可
恢复。实现一个严格有序的写入事务，以及 write_file / edit_file / patch_file 三个写工具。

## 任务分析

Day 11 建好了权限门（审批「是否允许」），但真正改文件还缺一个「如何安全地改」的流程。直接
`open(path, "w").write(...)` 有四个问题：

| 问题 | 后果 |
| --- | --- |
| 改前不可审 | 不知道要改什么就写了 |
| 改后不可追踪 | 出了错不知道改了啥 |
| 出问题不可恢复 | 没有修改前快照 |
| 覆盖他人并发修改 | 静默丢掉别人同期的工作 |

所以写入必须是一个严格有序的原子流程。

## 代码实现

### 1. apply_reviewed_change —— 受控写入事务

```python
def apply_reviewed_change(path, transform, approve, group_id):
    existed = path.exists()
    before_bytes = path.read_bytes() if existed else b""         # 读 before
    before_text = before_bytes.decode("utf-8")

    after_text = transform(before_text)                          # 构造 after（工具提供的变换）
    if after_text == before_text:
        return None                                              # 无变化短路

    diff_text = unified_diff(path.name, before_text, after_text)  # 生成 diff
    if not approve(diff_text):                                   # 审批（桥接 Day 11 权限）
        raise PermissionError("edit denied")

    current = path.read_bytes() if path.exists() else b""       # 审批后再读，哈希复查
    if digest(current) != digest(before_bytes):
        raise RuntimeError("file changed after review")          # 并发冲突，不覆盖

    checkpoint = Checkpoint(path=str(path), existed=existed, before=before_bytes,
                            before_hash=digest(before_bytes), after_hash=digest(after_bytes),
                            group_id=group_id)                   # 建 checkpoint
    atomic_write(path, after_bytes.encode("utf-8"))              # 原子写（临时文件 + replace）
    return checkpoint
```

**关键点**：

- 顺序是「读 before → transform 构造 after → diff → 审批 → 哈希复查 → 原子写」，每一步防一种
  风险；
- `transform` 是回调（`before → after`），由具体工具提供——write_file 是「完整替换」，edit_file
  是「单处替换」，patch_file 是「多处替换」。

### 2. 三个写工具的分工

```python
# write_file：新建/完整替换 → transform = 直接返回新内容
# edit_file：单处精确替换 → transform = 检查 old_string 出现次数，恰好 1 次才替换
# patch_file：多处有序替换 → transform = 按顺序应用所有 old→new，任一步失败整体抛错
```

## 讲解

### 每个模块为什么存在

| 模块 | 职责 | 失败条件 |
| --- | --- | --- |
| `file_review.py` | 统一事务（diff → 审批 → checkpoint → 复查 → 原子写） | — |
| `write_file` | 新建/完整替换 | 未授权、并发变化 |
| `edit_file` | 单处精确替换 | 零匹配、多匹配 |
| `patch_file` | 多处有序替换 | 任一步失败 |

### 数据流

```
模型提议「把 // 改成 /」
        │
        ▼
edit_file 工具 → apply_reviewed_change(path, transform(替换), approve, group_id)
        ├── 读 before + 哈希
        ├── transform 构造 after（零匹配/多匹配报错）
        ├── 生成 diff
        ├── approve 审批（桥接 EDIT 授权线）
        ├── 哈希复查（文件被外部改了 → RuntimeError）
        ├── 建 checkpoint（before + after_hash）
        └── atomic_write 原子落盘
        │
        ▼
返回 checkpoint（before 可恢复 + after_hash 供 Day 16 冲突检测）
```

### 关键设计决策

1. **为什么 Checkpoint 的 before 存 bytes 而非 str？** `bytes` 能精确保留原始字节——编码、换行
  （Windows 的 `\r\n`）、BOM；若 decode 成 str 再存，恢复时 encode 往返可能丢失信息。Checkpoint
  是要「逐字节还原」的，所以必须存 bytes。

2. **为什么审批后要再做一次哈希复查？** 审批不是瞬间完成的，中间隔着「用户犹豫、走开、回来」
  的时间窗口，窗口里文件可能被外部改过。如果不复查，就会用「旧 before 构造的 after」覆盖掉
  窗口内的新改动。再读一次 + 哈希对比，是「乐观并发」的检测——宁可失败也不覆盖别人的工作。

3. **为什么精确编辑优于整文件重写？** 精确编辑能保留未读内容、换行风格和用户的并发修改，也
  更容易生成可靠的 Diff；整文件重写会覆盖掉用户同一时刻的其他改动。Agent 要「懂上下文地改」，
  而不是「推倒重来」。

4. **为什么用 mkstemp + os.replace 原子写入？** `mkstemp(dir=path.parent)` 保证临时文件与目标
  在同一文件系统，`os.replace` 才能原子替换；原子替换避免「写一半崩溃留下半成品」。

## 写入事务在 Agent Runtime 里的位置

写入事务是「**改文件**」这一环的安全流程，它夹在「权限审批」和「真实落盘」之间：

- **上游是 Day 11 的权限**：`approve` 回调桥接到 `PermissionManager` 的 EDIT 授权线——先审批
  再写，无权限层默认拒绝；
- **下游是文件系统**：通过 `resolve_workspace_path`（Day 6）确认路径在工作区内，通过
  `atomic_write` 原子落盘；
- **产物是 Checkpoint**：每次写入留下 `before`（原始内容）+ `after_hash`（写入后哈希），这份
  「修改前快照」正是 Day 16 rewind 回退时需要的「恢复凭据」。

所以 Day 12 不只是「能写文件」，更是把「写」这件事做成了一条**可审查、可追踪、可恢复、
不覆盖他人**的安全链。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 拒绝时文件逐字节不变 | ✅ write_file 审批拒绝后内容不变 |
| 零匹配 / 多匹配严格失败 | ✅ edit_file 分别报错 |
| 补丁中途失败整体不落盘 | ✅ patch_file 任一步失败抛错、不写入 |
| 新建文件 Checkpoint existed=False | ✅ write_file 新建场景 |
| 已有文件保存原始内容可恢复 | ✅ Checkpoint.before 存 bytes |
| 审批后外部修改触发冲突 | ✅ 哈希复查抛 RuntimeError |
| 既有测试无回归 | ✅ 72 passed |

## 小结

Day 12 是「第一次允许 Agent 改文件」，但它把重点放在「怎么安全地改」而不是「能不能改」。
核心是一套严格有序的原子事务：读 before → 构造 after → diff → 审批 → checkpoint → 哈希复查
→ 原子写。这套事务把「改前可审查、改后可追踪、出问题可恢复、不覆盖他人」四个目标一次性
达成。三个写工具的「失败条件」（零匹配、多匹配、补丁中途失败）都设计成「宁可失败也不乱写」。
这套事务和 checkpoint 会被 Day 16 的 rewind 直接复用——回退就是「用 checkpoint 的 before 反向
执行一次写入」。
