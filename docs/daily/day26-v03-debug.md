# v03 集成测试调试全记录：子 Agent 委派从「跑不通」到「跑通」

## 这份文档是什么

`tests/v03_subagent_harness.py` 是 Day 26 的**真实模型集成测试**——用 DeepSeek 跑通「父
Agent 调 task 工具并发委派两个 explore 子 Agent 探索代码库」的端到端流程。本文档记录它
从「跑不通」到「两个子 Agent 都 done」的**完整调试过程**：每一次运行的现象、根因、怎么
一步步改对的。每一个坑都是「真实模型集成测试」的典型问题，值得反复回看。

## 测试目标

父 Agent 收到「委派两个 explore 子 Agent 探索代码库」的任务，应当：
1. 调 `task` 工具，一次委派两个只读 explore 子 Agent；
2. 两个子 Agent 各自递归跑 agent loop，读文件、写报告；
3. 两个子 Agent 都 `stop=done`，task 工具返回 `ok=True`；
4. 父 Agent 汇总子报告，`stop_reason=done`。

---

## 第 1 次运行：mock 模型（成本 0 次调用）

### 现象

```
C:\Anaconda\envs\minicode\python.exe D:\Project\minicode-lite\tests\v03_subagent_harness.py
    [Kernel拦截] verification_missing: 运行与当前文件版本相关的验证后再结束。
    ...（重复 10 次）
=== 结果 ===
  stop_reason: max_steps
  成本: 调用 0 次, 输入 0, 输出 0, $0.000000
```

两个反常信号：`成本: 调用 0 次`（真实模型不可能 0 次调用）+ `verification_missing` 死循环。

### 根因

**「成本 0 次调用」= 跑的是 mock 模型。** `load_runtime_config()` 里 `load_dotenv(".env")`
是**相对当前工作目录**读的。用户用绝对路径跑 v03，但 shell 的 cwd 不在项目根，`D:\...\.env`
读不到，`MINI_CODE_MODEL` 缺省退回 `"mock"` → `build_adapter` 返回 `MockModelAdapter`。
MockModelAdapter 不经过 CostTracker，所以成本 0。

**`verification_missing` 死循环**：mock 下 `classify_intent_with_model` 退化成 `parse_intent`
关键词匹配，「探索/委派/汇总」都不在 `_SEARCH_WORDS` 里，兜底成 `CODE`，`requires_verify=True`
→ 父 Agent 想 final 但被 Kernel 拦截「先验证再结束」，死循环到 max_steps。

### 修复

```python
# v03_subagent_harness.py main() 开头，显式加载项目根的 .env，不依赖 cwd
load_dotenv(str(Path(__file__).resolve().parents[1] / ".env"))
```

（真实模型分类会把「探索代码库」正确判成 SEARCH，`verification_missing` 自动消失。）

### 结果

真实模型生效（成本 > 0），但暴露了下一个坑。

---

## 第 2 次运行：workspace root 错误

### 现象

```
    [工具] list_files({'path': 'minicode'})        → ok=False（not a directory）
    [工具] read_file({'path': 'minicode/agent_loop.py'}) → ok=False
    [工具] list_files({'path': '.'})               → ok=True
    [工具] read_file({'path': '../minicode/agent_loop.py'}) → ok=False
    [工具] git({'subcommand': 'rev-parse', ...})   → ok=True（返回 D:/Project/minicode-lite）
```

`list_files('.')` 成功、`list_files('minicode')` 失败、`read_file('../minicode/...')` 越界被拒。

### 根因

v03 的 `--cwd` 默认 `"."`。`resolve_workspace_path` 把 `context.cwd="."` resolve 成**用户
shell 的 cwd**，不是项目根。于是 workspace root 是 shell cwd，`minicode` 相对它不存在，
`../minicode` 越界（`PermissionError`）。子 Agent 继承了父 Agent 这个错误 cwd，所以 task 也
失败。

### 修复

```python
# v03_subagent_harness.py
parser.add_argument("--cwd", default=str(Path(__file__).resolve().parents[1]), help="工作目录（默认项目根）")
```

### 结果

`list_files('minicode')` / `read_file('minicode/...')` 正常了。

---

## 第 3 次运行：task ok=False，但看不到错误内容

### 现象

```
    [工具] task({'tasks': [...]})
    [工具结果] task ok=False          ← 只有 ok，没有错误内容
```

父 Agent 看到 task 失败后自己 fallback 探索，最终 `stop_reason=max_steps`。但**完全不知道 task
为什么失败**——是参数校验失败、子 Agent 超步数、还是异常？

另外两处误导：`最终回答: ...` 其实取的是 `history[-1]['content']`（最后一条消息，可能是
read_file 返回的源码），不保证是模型的 final；脚本正常结束（退出码 0）不代表测试成功。

### 根因

`on_tool_result` 只打印 `ok={r.ok}`，没打印 `r.output`；脚本没在 `stop != "done"` 时判定失败。

### 修复

```python
on_tool_result=lambda c, r: print(f"    [工具结果] {c.name} ok={r.ok}  {r.output[:800]!r}")
# main() 结尾
if stop != "done":
    raise SystemExit(f"测试失败：{stop}")
```

### 结果

下次 task 失败能看到具体原因了（见第 4 次）。

---

## 第 4 次运行：grep_files 单文件 bug + read_file 被外置死循环

### 现象（task 失败原因终于可见）

```
[工具结果] task ok=False  '[Sub-agents completed] 2 task(s), 28.0s
[探索 agent_loop.py] FAILED (stop=max_steps)
[探索 mcp.py] FAILED (stop=max_steps)'
```

两个子 Agent 都 max_steps。父 Agent fallback 时暴露出两个工具 bug：

```
[工具] grep_files({'pattern': '^(def|class|async def) ', 'path': 'minicode/agent_loop.py'}) → (no matches)
[工具] grep_files({'pattern': 'def ', 'path': 'minicode/agent_loop.py'})                    → (no matches)
[工具] grep_files({'pattern': 'agent_loop', 'path': 'minicode'})                            → 正常返回
[工具] read_file({'path': '.mini-code-tool-results/86e...txt'})                             → 读外置文件
```

### 根因

**grep 单文件 bug**：`grep_files` 用 `root.rglob("*")` 遍历，传入**文件**时
`Path("文件").rglob("*")` 返回空（文件没有子项），所以搜单文件永远 `(no matches)`。模型 grep
不到 `def` 结构，只能反复 read_file 翻源码。

**read_file 外置死循环**：`ToolResultBudgetManager` 的 `limit=2000`，而 read_file 默认返回
8000 字符 → read_file 结果**总是**被外置成「前 1000 + 后 1000 + artifact 路径」，模型读
artifact 又是 8000 字符、又被外置，永远拿不到完整内容。

### 修复

```python
# grep_files.py _run：支持单文件
if root.is_file():
    candidates = [root]; base = root.parent
else:
    candidates = root.rglob("*"); base = root

# agent_loop.py：read_file 绕过 tool_budget（已按 offset/limit 分页，模型可控读取量）
if call.name == "read_file":
    path = ...
    output, _ = read_dedup.process(path, output)
    budget_meta: dict = {}   # 不再经 tool_budget 外置
else:
    output, budget_meta = tool_budget.process(output)
```

### 结果

grep 能搜到单文件了，read_file 一页完整给模型。但还有下一个坑。

---

## 第 5 次运行：字符 vs 行号混淆 + 子 Agent 读小块

### 现象

父 Agent 读 `OFFSET=0 END=8000` 后，不去 offset=8000 继续，而是 `read_file(offset=95, limit=60)`
、`read_file(offset=120, limit=450)` —— 想读「第 95 行附近」。子 Agent 的 read_file 的 limit
是 **400**，父 Agent 是 4000。

### 根因

**字符 vs 行号混淆**：grep 返回 `file:95:内容`（**行号**），模型拿 95 当 read_file 的 offset
（**字符偏移**），读的是「第 95 个字符」不是「第 95 行」，拿不到目标反复读。

**子 Agent 读小块**：块大小是模型自己选的，受 system prompt 影响——父 Agent 的 build_prompt
有「用 offset/limit 分页读大文件」，explore 子 Agent 的 prompt 只有「quickly search」，模型
就保守读小块。`mcp.py` 27313 字符 ÷ 400 = **68 次**，10 步当然不够。

### 修复

```python
# read_file.py：schema 明确 offset 是字符偏移、limit 是字符数
"offset": {"type": "integer", "description": "字符偏移（不是行号），从 0 开始，默认 0"},
"limit": {"type": "integer", "description": "读取的字符数，默认 8000，最大 20000"},

# grep_files.py：返回匹配行 + 前后 2 行上下文（>> 标匹配行），模型不必再二次定位
# task.py explore/plan system_prompt：读文件用大 limit（4000-8000）读完整页
```

### 结果

grep 一次给上下文，模型不再拿行号当字符 offset；子 Agent 读大块。但 mcp.py 子 Agent 仍
max_steps，继续查。

---

## 第 6 次运行：子 Agent 模型看到完整工具清单

### 现象

GPT 分析指出：「配置集合本身不够，必须保证传给模型的工具 schema 只含这三个工具」。

### 根因

`OpenAIAdapter` 的 `self._tools` 在 `__init__` 冻结，`next()` 每轮都用它。子 Agent 复用父
adapter（`context.model`），所以模型 `next()` 传给 API 的工具 schema 是**完整清单**（能看到
write_file/run_command），只是「执行」用过滤后的 sub_tools 拒绝。参考项目是「为子 Agent 新建
tools=过滤后的 adapter」。

### 修复

```python
# openai_adapter.py：with_tools 返回同配置 + 新工具清单的浅拷贝
def with_tools(self, tools):
    return OpenAIAdapter(model=self._model, base_url=self._base_url, api_key=self._api_key,
                         tools=tools, cost_tracker=self._cost_tracker, ...)

# task.py _run_subagent：子 Agent 用过滤后的 adapter
sub_model = model.with_tools(filtered) if hasattr(model, "with_tools") else model
```

### 结果

explore/plan 的模型现在只看到 3 个只读工具，不会再尝试调 write_file/run_command。

---

## 第 7 次运行：announce_budget 的 off-by-one + 「强制」是软约束

### 现象

子 Agent 每轮注入「Remaining turns」，但数字少 1；「最后一轮强制报告」没生效——模型最后
一轮还是请求工具，执行完就 max_steps。

### 根因

`remaining = max_steps - step_idx - 1` 是「本轮之后还剩几轮」，提示语却写「including this
turn」，数字少 1。而且「final turn 别再请求工具」是**软提示**，模型可能不理会。

### 修复

```python
# agent_loop.py upsert_budget_hint：含本轮则 +1
hint = f"Remaining model turns: {remaining + 1}, including this turn. ..."
```

（「强制报告」本质是软约束，模型不配合时无法真正强制。真正的收敛靠下一个坑。）

---

## 第 8 次运行：子 Agent 用 flash（最终收敛）

### 现象

pro 下 mcp.py 子 Agent 反复 max_steps——它 grep 了 `minicode/`、`tooling.py`、`docs/`、
`DECISIONS.md` 一堆目录找关联，15 步用完还没报告。

### 根因

pro 是旗舰推理模型，对「探索类」子任务「过度认真」，15 步不够它收敛；探索/规划类子任务
「量大、对深度要求低」，本就不需要旗舰模型。

### 修复

```python
# openai_adapter.py：with_model 返回同配置 + 新模型名的浅拷贝
def with_model(self, model):
    return OpenAIAdapter(model=model, base_url=self._base_url, api_key=self._api_key, ...)

# task.py：子 Agent 换 flash，父 Agent 保持 pro
_SUBAGENT_MODEL = "deepseek-v4-flash"
sub_model = sub_model.with_model(_SUBAGENT_MODEL)
```

### 结果（最终）

```
[子Agent:探索 agent_loop.py] 结束 stop=done
[子Agent:探索 mcp.py] 结束 stop=done
[工具结果] task ok=True
stop_reason: done
成本: 调用 23 次, 输入 166551, 输出 10248, $0.325462
```

两个子 Agent 都 done，task `ok=True`，父 Agent `done`，成本 **$0.325**（pro 的 $0.6 便宜近
半），耗时 81.5s（120s 快 1/3）。

---

## 方法论总结：真实模型集成测试的 11 条教训

1. **「成本 0 次调用」= 跑的是 mock 模型**。真实模型 harness 必须显式定位项目根的 `.env`，
   不能依赖 cwd。
2. **意图分类的 fallback 关键词要覆盖「只读探索类」动词**，否则「探索」被误判成 CODE，
   「改代码要验证」的门槛误伤只读任务。
3. **harness 的 cwd 不能默认 `.`**——工具执行和边界检查都依赖 cwd，必须显式给绝对路径。
4. **集成测试要打印失败详情 + 用退出码/断言表达成败**，不能只看「脚本正常结束」。
5. **「搜索文件内容」的工具必须同时支持单文件与目录**，否则模型 grep 不到结构只能反复 read。
6. **分页读取工具（read_file）和「大结果外置」别打架**——read_file 的一页要完整给模型，
   外置只针对「一次性大输出」。
7. **工具输出的定位信息要和下游工具的定位参数单位一致**（行号 vs 字符偏移），否则必然被
   模型误用；给上下文比让模型二次精确定位更稳。
8. **读文件的块大小策略要写进子 Agent 的 system prompt**，否则模型默认读小块。
9. **「只读」要彻底**：既要执行层过滤（sub_tools），也要模型层过滤（with_tools 让模型也
   看不到写工具）。
10. **预算感知是「提示 + 模型配合」不是硬保证**——`announce_budget` 动态注入剩余轮数，
    但「最后一轮强制报告」是软约束，模型不配合就失效。
11. **探索/规划类子任务用低成本快速模型（flash）**：量大、深度要求低，匹配 flash；实测
    flash 下收敛更好、更便宜、更快。这是「按任务类型选模型」（模型路由）的雏形。
