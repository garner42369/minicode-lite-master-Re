# Day 11：权限系统——“模型想做”与“程序允许做”之间的硬边界

## 今日任务

真实模型已经能提议动作（Day 10），但还不能让它自动改文件、执行命令。今天建立路径、命令、
编辑三条授权线，以及 plan / default / auto / bypass 四种运行模式。

## 任务分析

Day 9 已强调「Prompt 不是授权」——提示词只是对模型的请求，模型可能遵守也可能不遵守。真正
的授权必须发生在权限层：模型提议的动作，在真正执行前，经过一次结构化的审批，得到一个明确
的裁决。

权限不是简单的 True/False。交互式产品至少需要「一次性 / 本轮 / 永久」的允许与「一次性 /
带反馈 / 永久」的拒绝，共六种决策。持久授权必须绑定明确 scope，不能因为允许写一个文件就
放开整个磁盘。

## 代码实现

### 1. PermissionManager.check —— 四模式 + 六决策 + 授权记忆

```python
class PermissionManager:
    def check(self, request) -> PermissionDecision:
        key = (request.kind, request.scope)

        # 1. bypass：用户明确授权的隔离环境，跳过一切审批
        if self.mode is Mode.BYPASS:
            return PermissionDecision(verdict="allow_once")

        # 2. plan：只读硬约束——只有安全读取放行，写入/命令/编辑一律拒绝
        if self.mode is Mode.PLAN:
            is_safe_read = request.kind is PermissionKind.PATH and request.risk in {SAFE, LOW}
            return PermissionDecision(verdict="allow_once" if is_safe_read else "deny_once")

        # 3~5. 记忆检查：永久拒绝 > 永久允许 > 本轮允许
        if key in self._session_deny:   return PermissionDecision(verdict="deny_always")
        if key in self._session_allow:  return PermissionDecision(verdict="allow_once")
        if key in self._turn_scopes:    return PermissionDecision(verdict="allow_once")

        # 6. auto：安全/低风险自动放行
        if self.mode is Mode.AUTO and request.risk in {SAFE, LOW}:
            return PermissionDecision(verdict="allow_once")

        # 7. 无 prompt 默认拒绝（fail-closed，绝不永久等待）
        if self.prompt is None:
            return PermissionDecision(verdict="deny_once")

        # 8. 询问用户，并按寿命记忆（allow_turn/allow_always/deny_always）
        decision = self.prompt(request)
        if decision.verdict == "allow_turn":    self._turn_scopes.add(key)
        elif decision.verdict == "allow_always": self._session_allow.add(key)
        elif decision.verdict == "deny_always":  self._session_deny.add(key)
        return decision
```

**关键点**：

- 决策顺序是「bypass → plan → 记忆 → auto → 默认拒绝 → prompt」，每一步都在防一种情况；
- 授权记忆用 `(kind, scope)` 二元组做键，`allow_turn` 记在本轮、`allow_always`/`deny_always`
  记在会话——三种寿命对应「一次性 / 本轮 / 永久」。

## 讲解

### 每个模块为什么存在

| 模块 | 职责 | 谁消费 |
| --- | --- | --- |
| `permissions.py` | 数据模型 + `PermissionManager`（四模式/六决策/授权记忆） | 写工具（Day 12）、命令（Day 13） |
| `auto_mode.py` | 命令风险分类（危险片段升级、强制提示原因） | 命令执行（Day 13） |
| `workspace.py` | 路径边界判断（大小写/盘符/UNC） | 所有文件工具 |

### 数据流

```
模型提议「写文件 X」
        │
        ▼
工具（write_file）构造 PermissionRequest(kind=EDIT, scope=X, risk=medium)
        │
        ▼
PermissionManager.check(request)
        ├── 模式判断（plan 拒绝 / auto 看风险 / bypass 放行）
        ├── 记忆检查（之前 allow/deny 过没有）
        └── prompt 询问用户 → allow_once / deny_with_feedback / ...
        │
        ▼
PermissionDecision(verdict, feedback) → 工具据此「写」或「不写」
```

### 关键设计决策

1. **为什么权限是「硬边界」而不是「提示词约定」？** 无论 prompt 说什么，执行前都要过
   `PermissionManager.check()`。自然语言约定是「请求」，结构化审批是「强制」，二者分离才能
   保证一个被注入的恶意指令文件无法诱导模型越权。

2. **为什么决策要区分「一次性 / 本轮 / 永久」三种寿命？** 用户说「允许写这个文件」时，语义
   是模糊的——是这一次、本轮、还是永远？若不区分，要么每次重复询问（体验差），要么一次性
   放开过宽（风险大）。三种寿命 + 对应的 deny，覆盖用户真实想表达的范围。

3. **为什么风险分类不等于 OS 沙箱？** 正则能识别「看似危险」的命令片段（rm -rf、sudo），但
   无法阻止一个「看似安全」的 Python 脚本访问任意系统资源。权限层是产品安全门，不是 OS 隔离。
   prompt injection 检测也只是风险信号，不能因命中某短语就永久拒绝合法任务。

4. **为什么整个权限层要「fail-closed」（默认拒绝）？** 无 prompt 默认 deny、无权限层默认拒绝、
   风险不明默认当危险。安全边界的第一原则是「宁可错杀、不可放过」——如果默认「不确定就放行」，
   任何「漏配了权限层」的场景都会变成「有副作用的动作静默执行」。默认拒绝让安全漏洞表现为
   「功能被卡住」（明显、可修），而不是「悄悄放行危险动作」（隐蔽、难查）。

## 权限在 Agent Runtime 里的位置

权限系统是整个安全模型的核心，也是 Day 9「Prompt 不是授权」这条红线的落地。它在 runtime 里
的位置是「**执行前的最后一道闸**」：

- **Day 12 的写入**：write_file/edit_file/patch_file 执行前，构造 EDIT 的 `PermissionRequest`
  走 `check()`——「改文件」这个动作被 EDIT 授权线拦住；
- **Day 13 的命令**：run_command 执行前，构造 COMMAND 的 `PermissionRequest` 走 `check()`——
  「执行命令」被 COMMAND 授权线拦住，`auto_mode` 的风险分类在这里决定「该不该问」；
- **四种模式覆盖整个信任光谱**：plan（完全不放权，只读）、default（每个副作用都问）、auto
  （安全自动过、危险仍问）、bypass（完全放权，仅隔离环境）。

权限通过 `ToolContext.permissions` 传给工具，工具在执行前先 `check()`，拿到 allow 才真正动手。
所以 Day 11 立的不是「一次性代码」，而是「整个安全体系的地基」——后续每个有副作用的动作
（写文件、执行命令）都要经过它这道门。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 四模式对同一动作的不同裁决 | ✅ plan 只读 / auto 安全自动过 / default 询问 / bypass 全过 |
| 六种决策与授权寿命记忆 | ✅ allow_turn 本轮记忆、allow/deny_always 会话记忆 |
| Windows 路径大小写/盘符/UNC | ✅ workspace_demo 七类边界全部正确 |
| 危险命令升级风险 + 强制提示原因 | ✅ rm -rf → dangerous |
| deny_with_feedback 文字进上下文、不改配置 | ✅ feedback 独立于配置 |
| 无 prompt 默认拒绝 | ✅ headless 不挂死 |
| 既有测试无回归 | ✅ 72 passed |

## 小结

Day 11 是整个安全模型的核心，也是 Day 9「Prompt 不是授权」这条红线的落地。它把「模型想做」
和「程序允许做」之间划了一道硬边界：模型提议的动作，执行前必须过 `PermissionManager.check()`
拿到明确裁决。四种模式覆盖「从完全不信任到完全信任」的信任光谱，六种决策覆盖用户真实想
表达的授权范围，三条授权线（路径/命令/编辑）各自绑定到正确的最小粒度。这一层会被 Day 12
的写入、Day 13 的命令执行直接复用——写文件要过 EDIT 授权线，执行命令要过 COMMAND 授权线。
