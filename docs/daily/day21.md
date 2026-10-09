# Day 21：CLI、Headless 与用户画像——同一内核的不同使用方式

## 今日任务

核心能力（Day 7-20）已经存在，今天把它包装成**可日常使用的命令行产品**，并保证
交互入口（CLI）和自动化入口（Headless）**共享同一个运行时**。同时引入用户画像
（USER.md），让模型记住用户的语言、解释深度、代码风格偏好。

## 任务分析

### 这是「第四阶段」的开端

前三个阶段（基础能力 → 核心能力）都是「补内核」；从 Day 21 起进入「交互界面与扩展
生态（Day 21-26）」，做的是「**包装内核**」——不再回头补 Agent 逻辑。

### 入口只装配依赖，不复制 Agent 逻辑

课件把入口的职责钉死为一条装配链：

```
解析参数 → 解析工作区 → 加载配置 → 初始化日志
→ 创建工具/权限/模型/记忆/会话/上下文 → 构建 Prompt
→ 调用共享 Runtime → 输出事件到当前界面 → dispose 资源返回退出码
```

任何「如何判断完成、如何执行工具」的逻辑出现在入口里，都说明边界泄漏了——那些逻辑
在 Loop / Kernel 里，入口不该重复。

### CLI 命令分两类

| 类型 | 示例 | 是否进模型 |
| --- | --- | --- |
| 本地管理命令 | `/sessions` `/memory` `/model` `/readiness` | 否 |
| 用户任务 | 「修复这个测试」 | 是 |

本地命令是「管理面」，不进模型——白花 token 且语义不对。

## 代码实现

### 1. 共享装配（`RuntimeFactory` + `Runtime`）

```python
@dataclass
class Runtime:
    cwd: str
    adapter: ModelAdapter
    tools: ToolRegistry
    permissions: PermissionManager
    cost_tracker: CostTracker

    def run(self, prompt, *, task=None, on_tool_start=None, ...):
        resolved_task = task or build_task_object(prompt, model=self.adapter)
        messages = [{"role": "system", "content": build_prompt(self.cwd)},
                    {"role": "user", "content": prompt}]
        return run_agent_turn(self.adapter, self.tools, messages, self.cwd,
                              task=resolved_task, permissions=self.permissions, ...)

    def dispose(self):
        self.tools.dispose()


class RuntimeFactory:
    def __init__(self, mode=Mode.AUTO, prompt=None):
        self.mode, self.prompt = mode, prompt

    def create(self, cwd) -> Runtime:
        config = load_runtime_config()
        tools = create_full_tool_registry()
        tracker = CostTracker()
        adapter = build_adapter(config, tools=tools.list(), cost_tracker=tracker)
        permissions = PermissionManager(cwd, self.mode, prompt=self.prompt)
        return Runtime(str(cwd), adapter, tools, permissions, tracker)
```

**策略**：CLI 和 Headless 复用同一个 `RuntimeFactory.create(cwd)`，不各自写一套装配。
`Runtime` 只持有「依赖」，不掺业务判断。

### 2. Headless（`headless.py`）—— 稳定退出码

```python
EXIT_OK = 0              # 成功（stop_reason == done）
EXIT_RUNTIME_ERROR = 1   # 运行时错误（未完成 / 异常）
EXIT_CONFIG_ERROR = 2    # 配置错误（如 base_url 缺失）
EXIT_UNAUTHORIZED = 3    # 未授权
```

**策略**：
- Headless 默认**拒绝副作用**（写/命令 deny），`--allow-edits` 显式允许且只对本次运行
  生效（用 `allow_once`，绝不持久化为「永远允许」）；
- 提供 `--json` 输出结构化结果、`--trace` 把工具调用打到 stderr；
- 退出码是机器调用者的唯一可靠信号，见「易错点」。

### 3. CLI（`main.py`）—— 交互循环 + 本地命令路由

```python
def _local_command(text, cwd) -> str | None:
    t = text.strip()
    if t == "/model": return f"model: {load_runtime_config().model}"
    if t == "/readiness": return "ready" if not validate_config(...) else "not ready: ..."
    if t == "/memory": return "\n".join(f"- {e.content}" for e in MemoryManager(cwd).list(PROJECT))
    if t == "/sessions": ...
    if t.startswith("/"): return f"unknown command: {t}"
    return None  # 用户任务，进模型
```

**策略**：本地命令直接处理、不进模型；用户任务进 `runtime.run`。审批 prompt 用交互式
`input()`，中英文都认（`y/yes/allow/是/允许/好`）。

### 4. 用户画像（`user_profile.py`）

```python
@dataclass
class UserProfile:
    language: str = ""        # 语言（如「中文」）
    verbosity: str = ""       # 解释深度（如「简洁」）
    coding_style: str = ""    # 代码风格（如「snake_case」）

def parse_user_md(content) -> UserProfile:   # 解析 ## preferences 段的 key: value
def to_prompt_section(profile) -> str:       # 转成 ## User Profile 段，空画像返回空串
def load_user_profile() -> UserProfile:      # 读 ~/.mini-code/USER.md，失败返回空画像
```

**策略**：USER.md 放全局 `~/.mini-code/USER.md`（跨项目）。注入位置在 `build_prompt` 的
动态区（安全约束、协议、指令之后），保证「**偏好低于系统安全**」——用户说「多解释」不能
越过「别泄露密钥」这类红线。

## 讲解

### 数据流

```
CLI（交互）──┐
             ├──▶ RuntimeFactory.create(cwd) ──▶ Runtime（config/adapter/工具/权限）
Headless（单次）┘                                    │
                                              Runtime.run(prompt)
                                                    │
                                   run_agent_turn（Loop，含压缩/记忆/验证门槛）
                                                    │
                              结果 ──▶ CLI 打印 / Headless 输出 + 退出码
```

### 关键设计决策

1. **为什么入口只装配、不复制 Agent 逻辑？** Agent 逻辑（如何判断完成、如何执行工具）
   在 Loop / Kernel 里，入口重复一份就产生两份真相，改一处漏一处。装配链把「依赖的
   创建顺序」收进 RuntimeFactory，两个入口共享。
2. **为什么本地命令不进模型？** `/model` `/memory` 这些是「管理面」——模型不该参与
   「显示当前模型」这种确定性的本地查询。进模型白花 token，且语义错位。
3. **为什么 Headless 要稳定退出码？** 机器调用者（CI/脚本）无法「看文本判断成败」，
   只能靠退出码。0/1/2/3 把「成功/运行时错误/配置错误/未授权」四类结果固定下来。
4. **为什么偏好低于安全？** 用户画像是「偏好」（希望模型怎么做），系统安全是「约束」
   （模型绝对不能怎么做）。约束永远高于偏好——用户说「多解释」不能变成「绕过权限」。

### 易错点：Error 文本 ≠ 成功

不要把 `Error: ...` 当普通成功文本从 Headless 返回 0。机器调用者必须能依靠退出码和
结构化状态判断任务结果——否则脚本会把「任务失败」当成「任务成功」继续往下跑。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 同一任务 CLI 与 Headless 产生相同业务结果 | ✅ 共享 RuntimeFactory |
| Headless 默认不卡在审批输入 | ✅ 默认 deny，`--allow-edits` 显式放行 |
| 本地管理命令不计入模型 Token | ✅ `_local_command` 路由，不进模型 |
| 输入历史与 Session 职责不混淆 | ✅ `InputHistory` 只存用户输入（去重） |
| 资源无论成功失败都 dispose | ✅ `Runtime.dispose()` 在 finally 里 |
| 偏好低于系统安全 | ✅ User Profile 注入在 safety/protocol 之后（有测试） |
| 既有测试无回归 | ✅ 126 passed |
