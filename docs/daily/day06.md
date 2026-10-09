# Day 6：工作区边界与只读工具——让 Agent 先学会观察

## 今日任务

给 Agent 装上「眼睛」，但先不给「手」。实现三个只读工具（`list_files` 看目录、
`read_file` 分页读、`grep_files` 搜索），以及所有文件访问的唯一入口
`resolve_workspace_path`（统一路径解析 + 越界检查）。

## 任务分析

**为什么先只读、不给写？** Agent 在改代码前，必须先可靠地**定位**文件——「看到」才有「改」
的前提。只读阶段也是安全边界最容易建立的阶段：没有任何写操作、命令执行，风险最小。这一天
的价值在于把「观察能力」和「安全边界」先做扎实，后面 Day 12 加写操作时才有可靠的地基。

**为什么路径边界是重中之重？** 一旦允许 Agent 读文件，第一个风险就是「读到工作区外的东西」
——`../` 穿越、绝对路径、符号链接指向外部。所以所有文件访问必须收敛到**一个入口**
`resolve_workspace_path`，边界只在这一处检查。

## 代码实现

### 1. resolve_workspace_path —— 路径安全的关键顺序

```python
def resolve_workspace_path(context, raw_path):
    root = Path(context.cwd).resolve()                          # 工作区根（真实路径）
    candidate = Path(raw_path)
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()  # 消解 .. 和符号链接
    try:
        target.relative_to(root)                                # 按「路径组件」判断是否在根内
    except ValueError as exc:
        raise PermissionError(f"path escapes workspace: {raw_path}") from exc
    return target
```

**关键点**：

- 顺序是「**先 resolve 再 relative_to**」：`resolve()` 把 `..`、符号链接消解成真实路径，
  `relative_to(root)` 按「路径组件」判断是否在工作区内（不是字符串前缀判断，`/foo` 不会误
  匹配 `/foobar`）。顺序反了，`../` 和符号链接就能绕过边界。

### 2. 三个只读工具 —— list / read / grep 的分工

```python
# list_files：看某一层目录，输出 "dir xxx" / "file xxx"，限制 200 条
# read_file：按 offset/limit 分页读，带进度头
header = f"OFFSET={start} END={end} TOTAL={len(content)} TRUNCATED={end < len(content)}\n"
return ToolResult(ok=True, output=header + content[start:end])
# grep_files：递归搜所有文本文件（rglob），跳过 .git/__pycache__/.venv 等，200 条上限，
#            输出 "路径:行号:内容"
```

**关键点**：

- 三个工具的「输出规模、失败模式」完全不同，拆开让模型能「先 list 看结构 → grep 定位 →
  read 读正文」逐步缩小范围；
- `read_file` 的进度头 `OFFSET/END/TOTAL/TRUNCATED` 是「给模型看的进度条」，`TRUNCATED=True`
  时模型带着新 offset 继续读——这就是「大文件分页」的实现；
- `grep_files` 的三处克制（跳过无关目录、200 条上限、输出带位置）都是为了「找」能缩小范围。

## 讲解

### 每个模块为什么存在

| 能力 | 位置 | 谁消费 |
| --- | --- | --- |
| `resolve_workspace_path` | workspace.py | 所有只读/写工具（Day 6 读、Day 12 写、Day 16 回退） |
| `list_files` / `read_file` / `grep_files` | tools/ | Loop（模型探索工作区） |

### 数据流

```
模型请求 read_file("README.md")
        │
        ▼
resolve_workspace_path(context, "README.md")   ← 唯一闸门：越界就 PermissionError
        │
        ▼
read_file 的 run：读文件 → 分页 → 返回带进度头的内容
        │
        ▼
ToolResult（经 Day 5 的 Registry 截断/兜底）→ 回填 Loop
```

### 关键设计决策

1. **为什么拆成三个工具而不是一个「大文件工具」？** 三个只读工具的输出规模、失败模式完全
   不同：`list_files` 输出小（第一步探索）、`read_file` 大文件必须分页、`grep_files` 先缩小
   范围再读正文。合成一个大工具，输出难控制，模型也没法做「由粗到细」的探索。

2. **为什么「先 resolve 再 relative_to」这个顺序不能反？** 如果先做字符串检查、再 resolve，
   `../` 和符号链接会在 resolve 之前「看起来安全」——`../secret` 可能不触发前缀告警，但
   resolve 之后已经指向工作区外。判断的对象必须是「真实的、已消解的路径」。

3. **为什么 grep 要「克制」？** 递归搜所有文件但跳过 `.git`/`__pycache__`/`.venv` 等目录
   （海量无关内容），200 条上限（结果易爆炸），输出带位置（方便定位去 read_file）。共同目标：
   让「找」能缩小范围，而不是「一搜搜出一堆没用的」。

4. **为什么路径边界要收在一个入口？** 如果每个工具各自判断「路径在不在工作区内」，边界逻辑
   就散落各处、容易漏。收敛到 `resolve_workspace_path` 一个入口，保证「Agent 只能碰工作区内
   文件」这个不变式只在一处被强制。

## 路径边界在 Agent Runtime 里的位置

`resolve_workspace_path` 是**所有本地文件访问的唯一入口**，这个定位决定了它后面被谁复用：

- **Day 12 的写入**：write_file/edit_file/patch_file 写文件前，都要先 `resolve_workspace_path`
  拿到「工作区内的规范路径」，越界就拒绝——「写」和「读」共用同一道边界；
- **Day 16 的 rewind**：回退文件时同样要确认「目标文件在工作区内」；
- **只读工具本身**：list/read/grep 三个工具，每一个的 `run` 第一步都是 `resolve_workspace_path`。

所以它是「文件访问安全」的**唯一闸门**——不管是谁、不管要读写哪个文件，都得先过这道门，
确认「路径在工作区内」。Day 11 的路径授权线，也是在这个基础上再叠加「审批」——但「边界」
这一层，Day 6 就先立住了。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 中文路径和中文内容读取正常 | ✅ `test_read_file_chinese` |
| 不存在 / 二进制 / 错误编码有明确结果 | ✅ `not_found` / `binary_file` |
| `../`、绝对外部路径按策略拒绝 | ✅ `PermissionError` |
| 大文件能分页（`TRUNCATED` 继续读） | ✅ `test_read_file_pagination` |
| 全部测试通过 | ✅ `11 passed`（累计 `44 passed`） |

## 小结

Day 6 的核心是「先学会观察，再谈动手」。三个只读工具让 Agent 能可靠地定位文件，而
`resolve_workspace_path` 这一道「路径边界」是所有后续文件操作的安全地基——Day 12 的写入、
Day 16 的回退，全都建立在「文件访问必须先过边界检查」这个不变式上。今天把「怎么读」和
「读的边界在哪」都做扎实，后面加写操作时，边界已经在那里了，不需要重新设计安全。
