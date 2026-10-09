# Day 13：命令与测试反馈——把「我觉得修好了」变成运行证据

## 今日任务

修改完成不是任务完成。今天加入可超时、输出受限的命令执行（`run_command`），以及专门的测试
结果解析（`test_runner`），让 Agent 用真实的运行/测试证据证明任务完成，而不是靠自我感觉。

## 任务分析

Day 12 能让 Agent 改文件了，但「改了」不等于「修好了」。模型可能在没读文件、没测试、甚至
工具失败后就说「完成」。今天补上「执行」和「验证」两块：

| 块 | 作用 |
| --- | --- |
| `run_command` | 让 Agent 能真正执行命令（超时、截断、结构化结果） |
| `test_runner` | 让 Agent 能真正跑测试（解析通过/失败/错误/零测试） |
| `git` / `diff_viewer` | 给 Agent 只读的工作区证据 |

## 代码实现

### 1. run_command —— argv 执行 + 结构化结果

```python
def run_command(argv, cwd, *, timeout=30.0, output_limit=20_000):
    completed = subprocess.run(argv, cwd=Path(cwd), capture_output=True, timeout=timeout)
    stdout, cut_out = clip(completed.stdout.decode("utf-8", errors="replace"), output_limit)
    stderr, cut_err = clip(completed.stderr.decode("utf-8", errors="replace"), output_limit)
    return CommandResult(
        argv=tuple(argv), exit_code=completed.returncode,
        stdout=stdout, stderr=stderr,
        duration_ms=(perf_counter() - started) * 1000,
        truncated=cut_out or cut_err,
    )
```

**关键点**：

- `argv` 模式（`subprocess.run(argv, ...)` 不 `shell=True`）把「程序」和「参数」分开传递，无
  shell 注入面；
- `CommandResult.ok` = `exit_code == 0 and not timed_out`——**不看 stdout 内容**，只看退出码和
  超时，非零退出码即使输出看着正常也是失败。

### 2. test_runner —— 在 run_command 之上解析 pytest

```python
class TestSummary:
    passed: int; failed: int; errors: int; zero_tests: bool
    @property
    def ok(self):
        # 验证成功 = 有测试 + 无失败 + 无错误（零测试不算成功）
        return not self.zero_tests and self.failed == 0 and self.errors == 0

def _run(data, context):
    argv = [sys.executable, "-m", "pytest", "-q", data["path"]]
    result = run_command(argv, context.cwd)                  # 复用 run_command
    summary = parse_pytest_summary(result.stdout)            # 解析 passed/failed/error
    return ToolResult(ok=summary.ok, metadata={...passed/failed/zero_tests...})
```

**关键点**：`test_runner` 不是自己 `subprocess` 跑 pytest，而是复用 `run_command`——超时、截断、
结构化结果都是现成的，它只做「把 pytest 输出解析成结构化摘要」这个增量。

## 讲解

### 每个模块为什么存在

| 模块 | 职责 | 谁消费 |
| --- | --- | --- |
| `run_command` | 通用执行器（argv/shell、超时、结构化结果、截断） | test_runner、git、模型 |
| `test_runner` | 运行 pytest + 解析通过/失败/错误/零测试 | Loop、Day 14 验证门槛 |
| `git` / `diff_viewer` | 只读工作区证据 | 模型 |

### 数据流

```
Agent 改完代码
        │
        ▼
test_runner：run_command(["pytest", "-q", path]) ──► CommandResult(exit_code, stdout)
        │
        ▼
parse_pytest_summary(stdout) ──► TestSummary(passed, failed, errors, zero_tests)
        │
        ▼
ToolResult(ok = summary.ok)   ← 零测试/有失败/有错误 → ok=False
        │
        ▼
回填 Loop → 模型据「测试真过了/真没过」决定下一步（Day 14 的 Kernel 也据此判断能否结束）
```

### 关键设计决策

1. **为什么 argv 优先、shell 走显式模式？** `shell=True` 会引入 shell 注入面——一个拼进命令
   字符串的参数可能被 shell 解释成指令；argv 数组把「程序」和「参数」分开传递，无注入面。
   需要管道/重定向时才显式声明 shell 模式，且风险升级。

2. **为什么返回结构化结果而不是文本？** Runtime 不应从一段字符串里「猜」退出码——非零退出码
   即使 stdout 看起来正常也必须是失败。把成功与否编码成 `ok`（只看退出码和超时），而不是靠
   模型看文本自己判断。

3. **为什么零测试不算验证成功？** pytest 对「没有任何测试」的目录会**以退出码 0 正常结束**，
   只打印 `no tests ran`。如果只看退出码，就会把「一个测试都没跑」误判成「验证通过」。
   `TestSummary.ok` 显式要求 `not zero_tests`——「验证」的真谛是「真的有一条测试证据证明了
   改动是对的」，零测试连「证据」都没有。

4. **为什么输出 head/tail 截断？** 命令可能输出海量内容（编译日志、dump），整段塞进模型上下文
   会撑爆预算。命令输出的开头（标题/命令名）和结尾（错误/摘要）最有信息量，`clip` 保留首尾
   各一半、中间省略。

## 命令执行在 Agent Runtime 里的位置

命令执行 + 测试反馈，是「**验证**」这一环的落地，它和几个 Day 的关系是：

- **上游复用 Day 11 的权限**：`run_command` 执行前构造 COMMAND 的 `PermissionRequest` 走
  `check()`，`auto_mode` 的风险分类决定「该不该问」——「执行命令」被 COMMAND 授权线拦住；
- **下游支撑 Day 14 的验证门槛**：`test_runner` 跑出来的「passed/failed/zero_tests」，正是
  Day 14 Turn Kernel 判断「验证证据够不够」的依据——不是模型说「我修好了」就结束，而是
  `test_runner` 真跑出了「测试通过」的证据才算；
- **git / diff_viewer 提供只读证据**：Agent 修完代码后用 `git diff` 看「改了什么」、`git status`
  看「工作区状态」——只读的「工作区证据」，commit/push 则单独审批。

所以 Day 13 完成的是「**把『我觉得修好了』替换成『跑出来的证据』**」——补上了 Agent 闭环里
「验证」这一环，让 Day 14 的「验证门槛」有东西可判。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 非零退出码保持失败（stdout 看似正常也失败） | ✅ `ok=False exit=2` |
| 零测试不算验证成功 | ✅ `zero_tests` 时 `ok=False` |
| 大输出不塞满上下文 | ✅ head/tail 截断 + truncated 标记 |
| headless 未授权不启动有副作用命令 | ✅ 无权限层时 command_denied |
| shell 模式风险升级 | ✅ echo 从 safe 升到 high |
| 「失败 → 编辑 → 通过」闭环 | ✅ passed=1/failed=1 → 修复 → passed=2/failed=0 |
| 既有测试无回归 | ✅ 72 passed |

## 小结

Day 13 的核心是把「我觉得修好了」这个主观判断，替换成「跑出来的证据」这个客观事实。Day 12
能让 Agent 改文件了，但「改了」不等于「修好了」——今天补上「执行」和「验证」两块：`run_command`
让 Agent 能真正执行命令（带超时、截断、结构化结果），`test_runner` 让 Agent 能真正跑测试
（解析通过/失败/错误/零测试），`git`/`diff_viewer` 给 Agent 只读的工作区证据。这一天的
「失败 → 编辑 → 通过」闭环，是整个项目第一次「Agent 用证据证明自己修好了」的完整演示——
也直接铺垫了 Day 14 的验证门槛。
