# Day 26：后台任务、子 Agent 与任务图——扩展吞吐但保持主控

## 今日任务

长命令和大型代码探索不应永久阻塞主会话。今天加三种「不阻塞」的机制，课件强调「三种对象
不要混淆」：

| 对象 | 是什么 | 例子 | 生命周期 | 隔离方式 |
| --- | --- | --- | --- | --- |
| 后台进程 | OS 进程 | 长时间 pytest、构建 | 跨请求，靠 PID 核对 | 进程边界 |
| 子 Agent | 独立 agent 循环 | 独立探索某模块 | 一次委派内 | 独立 messages + 工具视图 |
| 任务图 | 逻辑依赖 DAG | 「分析→修改→验证」 | 跨会话（可持久化） | 文件所有权声明 |

## 背景：为什么需要「扩展吞吐」

Day 7 的 `run_agent_turn` 是**同步串行**的：一次任务，模型一步步执行，任何一步阻塞（长命令
跑 5 分钟、大探索读整个仓库）都会卡住整个会话。Day 26 在「不改变核心闭环」的前提下，给三
类「耗时/量大」的工作开了旁路：

1. **后台进程**——长命令不阻塞，丢给 OS 进程，父会话继续，之后查状态/读输出/取消；
2. **子 Agent**——大探索不占父上下文，委派给隔离的子循环，父 Agent 只收摘要（课件易错点：
   别把父会话原样复制给子 Agent，只传最小输入）；
3. **任务图**——「分析→修改→验证」这类多步编排，用依赖 DAG 显式建模，不必对应具体进程。

---

## 一、后台进程：`background_tasks.py`

### 为什么必须是 OS 进程，而不是线程/协程

后台任务的典型负载是「长时间 pytest、构建」——它们是**外部进程**（`subprocess` 启动的
`python -m pytest`、`make` 等），本来就有自己的 PID、stdout、退出码。minicode 要做的不是
「再包一层线程」，而是**登记并跟踪这些外部进程**：记下 PID，之后靠 PID 判断它是否还活着。

### 完整生命周期

```
start_background_task(command, cwd)
  → spawn 子进程（stdout 重定向到 .mini-code-task-output/<id>.log）
  → 登记 task_id / command / pid / status=running / started_at
  → 返回 BackgroundTask

list / get            → 查询并刷新状态（靠 PID 存活检测）
get_output(task_id)   → 读输出日志
cancel(task_id)       → taskkill /T /F（Windows 杀进程树）/ SIGTERM（Unix）
```

**为什么输出重定向到文件而不是管道**：后台进程的 stdout 可能很大（构建日志），且父会话
之后才来读。重定向到文件，`get_output` 按需读，不占内存、不阻塞。管道会因缓冲满而卡住
子进程。

### PID 存活检测（Windows 的坑）

```python
def _is_process_alive(pid):
    if sys.platform == "win32":
        # Windows 上 os.kill(pid, 0) 对任何情况都抛 OSError（包括进程存在），不可用
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle: return False          # 打不开 → 进程已死
        GetExitCodeProcess(handle, &code)
        return code == STILL_ACTIVE          # 259 = 还在运行
    else:
        os.kill(pid, 0)                      # Unix: signal 0 只查存在、不发信号
```

这是「重启后核对 PID」的关键：后台任务登记的是 PID，会话重启后 Popen 对象没了，只剩 PID。
`_is_process_alive` 只能回答「这个 PID 是否还活着」，**不能**回答「是否还是原来那个进程」
（PID 可能被复用）——要精确核对需对比进程启动时间，参考项目也没做，这里对齐「只查存活」。

### slot 限制（课件「达到并发上限明确拒绝」）

```python
_max_slots = 5
get_slot_stats() -> {"used_slots", "max_slots", "available_slots", "total_tracked"}
```

全局并发上限，防止模型/用户无节制地启动后台任务把机器拖垮。

---

## 二、任务图：`task_graph.py`

### 「定义」和「槽位」分离（核心设计哲学）

参考项目 task_graph 的注释点破了关键：**TaskDefinition（what，做什么）和 TaskSlot（who 在
跑、进度多少）是两个正交维度**。

- `TaskDefinition`：跨会话持久化的「任务是什么」——name/description/dependencies/priority/
  timeout。它回答「这张图要完成哪些事」。
- `TaskSlot`：会话内的「执行状态」——state/progress/started_at/completed_at/error/result。
  它回答「现在谁在跑、跑到哪了」。

两者分离，才能「保存/恢复任务图」而不把「运行中的进程状态」也序列化进去（进程状态是
session-local 的，重启即失效）。

### 依赖 DAG 的就绪判断

```python
def get_ready_tasks(self):
    done = {s.task_id for s in self.slots.values() if s.state is TaskState.DONE}
    running = {s.task_id for s in self.slots.values() if s.state is TaskState.RUNNING}
    available = max(0, self.max_slots - len(running))
    ready = [t for t in self.definitions.values()
             if t.id not in done and t.id not in running
             and all(dep in done for dep in t.dependencies)]
    ready.sort(key=lambda t: priority_order[t.priority])
    return ready[:available]
```

三层过滤：① 没完成、② 没在跑、③ 依赖全 DONE。再按优先级排序 + slot 上限截断。这是「分析→
修改→验证」顺序推进的引擎——`验证` 依赖 `修改` 依赖 `分析`，只有前驱 DONE 才就绪。

### 文件所有权（写隔离，不用 git worktree）

课件验收「多写任务不会无隔离修改同一文件」，原文是「独立 worktree **或**明确文件所有权」
（二选一）。参考项目用 `WorktreeIsolator`（git worktree），我们选**文件所有权**：

```python
class FileOwnership:
    def claim(self, task_id, files) -> list[str]:   # 返回冲突的文件列表
    def release(self, task_id): ...
    def owner_of(self, path) -> str | None: ...
```

写任务声明要写的文件，第二个任务声明已占用的文件 → 冲突拒绝。为什么不用 worktree：① 依赖
「工作区必须是 git repo」；② worktree 创建/清理复杂（orphan 分支、remove -f）；③ 参考项目
只做了隔离 + 清理，没做「改完合并回主工作区」。文件所有权轻、无 git 依赖、同步子 Agent 场景
够用。

---

## 三、子 Agent：`tools/task.py`

### 子 Agent 的任务合同

课件定义子 Agent 的合同（首版 explore/plan 强制只读）：

```text
goal：明确目标
inputs：允许读取的上下文（最小化，别复制父会话）
scope：工作区/文件/只读限制
budget：步数、时间、Token、费用
agent_type：explore | plan | general
output：结构化报告（summary / inspected / changed / risks / verification）
parent_trace_id：关联父任务
```

我们的落地：`task` 工具接收 `tasks` 列表（每个含 description/prompt/agent_type），内部为每个
任务构造隔离的子循环。

### 三种 agent_type

| 类型 | 工具集 | max_turns | 定位 |
| --- | --- | --- | --- |
| explore | list_files / read_file / grep_files | 15 | 快速只读探索 |
| plan | list_files / read_file / grep_files | 15 | 深入只读分析 |
| general | 全工具 | 15 | 全功能（可读写） |

explore/plan 强制只读是**双层**的（见下），general 才能写。

### 批内并发（方案 1）

```python
with ThreadPoolExecutor(max_workers=min(len(tasks), _MAX_CONCURRENT)) as pool:
    futures = [pool.submit(_run_subagent, t, ...) for t in tasks]
    results = [f.result() for f in futures]
```

一次 task 调用委派 N 个子任务，线程池并发跑，`f.result()` 阻塞等全部完成，父 Agent 拿到汇总
后继续。`_MAX_CONCURRENT = 4` 限制批内并发（课件「并发上限明确拒绝」）。

**为什么是多线程不是多进程**：子 Agent 的工作主要是调模型 API（I/O 密集），GIL 影响小；且
每个子 Agent 的 messages/tools 是独立局部变量，天然隔离，不需要进程级内存隔离。多进程反而
要解决「结果怎么传回父进程」的通信开销。

### 隔离：子 Agent 只看到该看到的

三个隔离维度：

1. **messages**：子 Agent 只有「自己的 system prompt + 任务 prompt」，不复制父会话（课件
   易错点：复制父会话浪费上下文 + 扩大敏感信息范围）。
2. **工具**：`ToolRegistry(filtered)` 只含 allowed 工具。
3. **模型视图**：`model.with_tools(filtered)` 让模型 `next()` 传给 API 的 schema 也只有这些
   工具——**执行层过滤不够，模型层也要过滤**，否则模型仍能看见 write_file/run_command 并
   尝试调用（被拒浪费步数）。

### 委派深度限制（防无限递归）

子 Agent 内部也可能调 task 工具（再委派孙 Agent）。用 `depth` 经 `ToolContext` 显式传递：

```python
if context.depth >= _MAX_DEPTH:      # _MAX_DEPTH = 2
    return ToolResult(ok=False, output=f"max depth reached")
sub_depth = context.depth + 1        # 传给子循环的 run_agent_turn(depth=sub_depth)
```

显式传参而非全局计数器，因为批内并发是多线程，全局计数器会有竞态。

### announce_budget：运行时预算感知

模型无法可靠计数「自己还剩几步」。`run_agent_turn(announce_budget=True)` 每轮在发请求前
upsert 一条 system 消息：

```
Remaining model turns: 5, including this turn. Finish only essential checks...
（最后一轮）This is your final model turn. Do not request more tools. Report now...
```

比提示词里写死「总预算 15」可靠——模型每轮都被告知精确的剩余轮数，接近上限就收敛写报告。

### 子 Agent 用 flash（模型路由雏形）

`_SUBAGENT_MODEL = "deepseek-v4-flash"`，子 Agent 用 `model.with_model(flash)`，父 Agent 保持
pro。探索/规划类子任务「量大、深度要求低」，匹配低成本快速模型；实测 flash 下子 Agent 收敛
更好（pro 会「过度认真」反复探索超步数）、成本省近半、速度快 1/3。这是「按任务类型选模型」
的雏形，Day 27 会系统化成真正的路由。

---

## 关键设计决策

1. **三种对象分开、不合并**：后台进程是 OS 进程（PID/取消），子 Agent 是隔离 agent 循环
   （messages/工具视图），任务图是逻辑 DAG（依赖/状态）。混在一起会让「进程」和「逻辑任务」
   耦合。
2. **子 Agent 用 flash、父 Agent 用 pro**：探索类子任务量大深度要求低，匹配低成本快速模型；
   父 Agent 主控用强模型。
3. **`with_tools`/`with_model` 浅拷贝 adapter**：子 Agent 要「同配置 + 不同工具/模型」，浅拷贝
   避免重建 base_url/api_key，且 `cost_tracker` 共享（成本记到同一个 tracker）。
4. **`announce_budget` 运行时注入**：比提示词写死「总预算」可靠，模型每轮知道精确剩余。
5. **grep 带上下文**：模型一次拿到匹配 + 上下文，避免「grep 行号当 read_file 字符偏移」的
   二次定位。
6. **文件所有权（不用 git worktree）**：写隔离靠「声明 + 冲突检查」，轻、不依赖 git。

## 易错点

- **别把父会话复制给子 Agent**：只传完成任务所需的最小输入，子 Agent 返回摘要 + 证据位置。
- **子 Agent 失败不当空成功**：`stop != done` 必须让 task 工具 `ok=False`，否则父 Agent 会把
  「没完成」当「完成了」。
- **只读要双层**：工具集过滤 + `with_tools` 模型视图过滤，少一层模型就会尝试调被拒的工具。
- **进程退出必须释放等待者**：后台任务/子 Agent 断线时，pending 请求不能永久挂起。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 取消父任务传播给子 Agent/后台进程 | ✅ 后台 cancel（taskkill 进程树）；子 Agent 深度 + 步数上限间接限制 |
| 子 Agent 失败不被当空成功 | ✅ `stop != done` → task `ok=False` |
| explore/plan 无法调用写工具 | ✅ 工具集过滤 + `with_tools` 模型也看不到 |
| 达到并发/递归上限明确拒绝 | ✅ `_MAX_DEPTH=2` + `_MAX_CONCURRENT=4` |
| 多写任务不无隔离修改同一文件 | ✅ `FileOwnership` 声明 + 冲突检查 |
| 真实模型端到端 | ✅ 两个子 Agent 都 done（调试过程见 `day26-v03-debug.md`） |
| 既有测试无回归 | ✅ 165 passed |

## 附：真实模型集成测试

「父 Agent 并发委派两个 explore 子 Agent」的端到端验证（`tests/v03_subagent_harness.py`）及
其完整调试过程（11 个坑）独立记录在 `docs/daily/day26-v03-debug.md`。
