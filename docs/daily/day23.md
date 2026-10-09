# Day 23：Skills 与 Hooks——让工作流可扩展，但不让扩展污染核心

## 今日任务

加入两类扩展，二者分开、不合并成一个「万能插件管理器」：

- **Skill**：供模型按需读取的工作流说明（一个目录 + 一份 `SKILL.md`）。
- **Hook**：运行时事件上的确定性自动化（日志 / 格式化 / 通知 / 策略检查）。

同时把 Day 20 还「直接调 MemoryPipeline」的权宜，重构成 `AGENT_START`/`AGENT_STOP`
hook 触发——让 Loop 彻底不再 import 记忆模块。

## 任务分析

### Skill 与 Hook 的本质区别

| | Skill | Hook |
| --- | --- | --- |
| 谁触发 | 模型判断「匹配任务」后主动 `load_skill` | 运行时在稳定事件点自动 fire |
| 是什么 | 指令来源（文本，给模型看） | 确定性代码（自动执行） |
| 权限 | 不授予权限，内容里的写/命令仍走权限门 | 可能做策略检查，但失败策略要明确 |

把两者合并成一个「插件」是错的：Skill 是「给模型的说明」，Hook 是「运行时的动作」，
生命周期和信任边界都不同。

### 三层优先级与发现/加载分离

Skill 搜索根四层（用户拍板「四根，含 .claude 兼容」）：

```
project   <cwd>/.mini-code/skills
user      ~/.mini-code/skills
compat_project <cwd>/.claude/skills     # 兼容 Claude Code 生态
compat_user    ~/.claude/skills
```

发现阶段只读摘要（name + description），全文只在 `load_skill` 时按需读——否则把所有
Skill 全文塞进 prompt，既撑爆上下文又让不相关 Skill 干扰模型。同名 skill 靠
`setdefault` 先到先得，实现高优先级根覆盖低优先级。

### description 提取：按课件，不兼容无 frontmatter

`extract_description` 只认 frontmatter 的 `description:` 前缀（课件 daily_code 的
`removeprefix("description:").strip()`），找不到返回空串，**不回退到「正文首段」**
（用户明确纠正：不要兼容无 frontmatter 的 SKILL.md）。参考项目是「正文首段」策略，
这里按课件执行。

### Hook 失败策略：只 continue

用户拍板「对齐参考项目，只 continue」——hook 异常只隔离、不阻断主流程，不做超时、
不做 block / fail-closed。参考项目 `hooks.py` 实际就是 try/except 隔离（没有 timeout、
没有 `FailurePolicy.BLOCK`），课件主文档列的「安全预检 fail-closed」留待未来需要时再
扩展。

## 代码实现

### 1. `hooks.py` —— 事件系统

```python
class HookEvent(str, Enum):      # 12 事件（对齐参考项目）
    PRE_TOOL_USE / POST_TOOL_USE / AGENT_START / AGENT_STOP /
    SUBAGENT_START / SUBAGENT_STOP / SESSION_SAVE / SESSION_RESUME /
    USER_INPUT / ASSISTANT_OUTPUT / STARTUP / SHUTDOWN

class HookManager:
    register(event, handler, description="") -> unregister
    fire_sync(event, **data) -> list[str]   # 同步执行，异常隔离，返回错误文本
    enable() / disable()
    get_stats(event=None) -> dict           # 注册数/启用数/调用数/失败数/明细
```

**关键设计**：`fire_sync` 的 `**data` 是可变字典，handler 可以往 `ctx.data` 里写结果
（如 `ctx.data["inject_box"]["project_text"] = ...`），fire 调用方通过传入的可变容器
读回。这让「有返回值的 hook」（记忆注入）不必改变 handler 签名 `Callable[[HookContext],
None]`，是「只 continue」策略下的干净传值方式。

### 2. `skills.py` —— 发现/加载/托管

```python
@dataclass(slots=True)
class SkillSummary:          # name / description / path / source（发现阶段，无全文）
class LoadedSkill(SkillSummary):  # + content

discover_skills(cwd) -> list[SkillSummary]   # 四根遍历，setdefault 先到先得
load_skill(cwd, name) -> LoadedSkill | None  # 按名读全文
install_skill(cwd, source, name, scope)      # 装进托管目录
remove_managed_skill(cwd, name, scope)       # 从托管目录删除
```

托管目录是 `.mini-code/skills`（project / user 两 scope），安装/删除只操作这里，不碰
source。

### 3. `tools/load_skill.py` —— 让模型按需加载

```python
LOAD_SKILL = ToolDefinition(
    validator=_validate,                 # name 必填、非空、去空白
    run=_run,                            # 从 context.cwd 取目录，调 load_skill
    metadata=ToolMetadata(
        name="load_skill",
        description="Load a local SKILL.md by name.",
        input_schema={...},
        capabilities={READ_ONLY, CONCURRENCY_SAFE},
    ),
)
```

**踩坑**：我们的 `ToolDefinition` 是 `(validator, run, metadata)` 结构（D8 把描述信息归
metadata），和参考项目的 `(name=, description=, input_schema=, ...)` 不同——照参考项目
写会直接错。且 `ToolContext` 自带 `cwd`，所以 load_skill 从 `context.cwd` 取目录，不绑
创建时 cwd，`create_full_tool_registry()` 无需改签名。

### 4. `agent_loop.py` —— fire hook，移除 MemoryPipeline

```python
# 发请求前（有 task 时，原 inject 位置）
inject_box: dict[str, str] = {}
if hooks is not None:
    hooks.fire_sync(HookEvent.AGENT_START, task=task,
                    context_ratio=context_ratio, inject_box=inject_box)
project_text = inject_box.get("project_text", "")
if project_text:
    history = upsert_project_memory(history, project_text)

# 任务结束统一出口
def _finish(history, reason):
    if hooks is not None:
        hooks.fire_sync(HookEvent.AGENT_STOP, task_success=(reason == "done"))
    return history, reason

# 工具前后
hooks.fire_sync(HookEvent.PRE_TOOL_USE, tool_name=..., tool_input=...)
hooks.fire_sync(HookEvent.POST_TOOL_USE, tool_name=..., tool_output=..., is_error=...)
```

`hooks is None` 时完全不 fire——扩展关闭时主链路照常（课件「扩展全部关闭主链路不变」）。

### 5. `memory_pipeline.py` —— 加 `register_hooks`，内聚接入知识

```python
def register_hooks(self, hooks) -> None:
    # inject 挂 AGENT_START、feedback 挂 AGENT_STOP
    hooks.register(HookEvent.AGENT_START, _on_start, description="memory inject")
    hooks.register(HookEvent.AGENT_STOP, _on_stop, description="memory feedback")
```

把「记忆流水线如何接入事件」的知识内聚在 MemoryPipeline 里，装配方（runtime_factory /
集成测试）只需调一次 `register_hooks`，不各自重复写闭包。

### 6. `runtime_factory.py` —— 装配 skills + hooks

```python
def create(self, cwd) -> Runtime:
    ...  # config/tools/tracker/adapter/permissions 不变
    skills = discover_skills(cwd)
    hooks = HookManager()
    MemoryPipeline(MemoryManager(cwd)).register_hooks(hooks)
    return Runtime(str(cwd), adapter, tools, permissions, tracker, skills, hooks)
```

`Runtime.run` 里 `build_prompt(self.cwd, skills=self.skills)`（补上 Day 21 漏传的
skills）并传 `hooks=self.hooks` 给 `run_agent_turn`。

### 7. `prompt.py` —— `_format_skill` 支持对象

```python
def _format_skill(skill) -> str:
    if isinstance(skill, str): return f"- {skill}"
    if isinstance(skill, dict): return f"- {name}: {description}"
    name = getattr(skill, "name", None)   # SkillSummary 等带 name/description 的对象
    if name is not None: return f"- {name}: {description}"
    return f"- (malformed skill entry: {skill!r})"
```

用 `getattr` 鸭子类型而非 import skills，避免 prompt 反向依赖 skill 模块。

## 讲解

### 数据流

```
模型任务 ──▶ run_agent_turn（Loop）
                 │ 发请求前：fire AGENT_START ──▶ memory hook 写 inject_box ──▶ upsert 注入
                 │ 工具前后：fire PRE/POST_TOOL_USE ──▶ 日志/策略 hook
                 │ 结束：fire AGENT_STOP ──▶ memory hook feedback（正/负强化）

Skill：发现（discover 摘要）─▶ prompt 注入「可用清单」─▶ 模型匹配 ─▶ load_skill 工具读全文
```

### 关键设计决策

1. **为什么把 MemoryPipeline 从 loop 挪到 hook？** Day 20 里 loop 直接 import
   MemoryPipeline 是权宜（当时还没有 hooks）。Day 23 有了 hook 机制，把「记忆注入/
   反馈」注册成 `AGENT_START`/`AGENT_STOP` 的 handler，Loop 就只依赖「事件」而不依赖
   「记忆模块」——记忆变成可插拔扩展，禁用后主链路不变。
2. **为什么 inject 结果经 inject_box 可变容器传回？** 「只 continue」策略下 handler
   签名是 `Callable[[HookContext], None]`（无返回值）。要让「记忆注入文本」从 hook 传回
   loop，最干净的方式是共享可变容器：loop 传入 `inject_box`，hook 往里写，loop 读。
3. **为什么把接入逻辑内聚成 `register_hooks`？** 否则 runtime_factory 和集成测试
   v02 都要复制同一段闭包。`register_hooks` 把「MemoryPipeline 挂哪些事件」收进一处，
   两处装配方各一行。
4. **为什么只 continue、不做 block？** 用户拍板对齐参考项目：hook 是辅助（日志/通知/
   策略检查），失败不该击穿 Agent 主循环。block（安全预检 fail-closed）是更强的承诺，
   需要 fire 能中断流程，当前不需要，留待未来。

### 易错点

- **外部 Skill/Hook 文件都是不受信内容**：Skill 里写「执行 `rm -rf`」不能绕过权限门；
  Hook 抛异常必须被隔离。`fire_sync` 的 try/except 和 `load_skill` 的只读能力标志就是
  这道边界。
- **`ToolDefinition` 结构不同**：我们是 `(validator, run, metadata)`，别照参考项目
  `(name=, description=, ...)` 写。
- **`hooks is None` 必须是零成本 noop**：loop 里所有 fire 都判空，保证不传 hooks 的
  旧调用方（如只读任务）行为完全不变。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 未匹配 Skill 不加载全文 | ✅ discover 只读摘要，load_skill 才读全文 |
| Skill 内容不能绕过权限 | ✅ load_skill 是 READ_ONLY 工具，写/命令仍走权限门 |
| 一个坏 Hook 不令全部 Hook 消失且诊断可见 | ✅ fire_sync 隔离 + errors 返回值 + get_stats（有测试） |
| 安全/通知 Hook 不同失败策略 | ⬜ 只 continue（D17，用户拍板暂不做 block） |
| 扩展全部关闭时离线主链路不变 | ✅ hooks=None noop + skill 空不注入（有测试） |
| 记忆注入/反馈重构为 hook 触发 | ✅ MemoryPipeline.register_hooks + loop fire |
| 既有测试无回归 | ✅ 145 passed（原 126 + 新增 19） |
