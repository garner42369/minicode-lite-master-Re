# Day 25：Product Surfaces 与 Capability Registry——让系统知道自己当前能做什么

## 今日任务

扩展越来越多后，「启动成功」不等于「产品真的可用」。今天建立统一的能力注册、指令层、
扩展清单、Hook 状态和 Readiness 快照，让系统（和模型、用户）能回答「我现在能做什么」。

## 背景：为什么要「自描述」

之前各能力散落各处：工具在 `ToolRegistry`、记忆在 `MemoryPipeline`、扩展在
`discover_skills`/`load_mcp_servers`、Hook 在 `HookManager`。这带来一个具体痛点：

> 用户 `clone` 一个仓库，`python -m minicode.main` 启动成功，然后问「帮我订张票」。
> 系统内部：MCP server 没连上、`base_url` 没配、但本地只读工具其实能用。用户看到的
> 却是「启动成功」四个字，然后任务莫名失败，没有任何一处能告诉他「哪些能力可用、
> 哪些不可用、为什么」。

诊断代码想回答「系统能不能干活」，只能一个个 `import`/`hasattr` 去猜——这既脆弱（模块
改名就破），又不一致（每个诊断方各猜一遍，猜出来的结果可能打架）。Day 25 把这种「猜」
收成一个**统一的能力目录 + 可序列化快照**：一处收集、多处（Prompt/CLI/Session）共用。

## 核心洞察：两个正交的维度

Day 25 其实是两个**独立**的东西，各自的职责边界要分清（参考 GPT 对参考源码的分析，我们
认同这个分界）：

| 维度 | 文件 | 回答的问题 | 例子 |
| --- | --- | --- | --- |
| 能力目录 | `capability_registry.py` | **有哪些能力**，各属什么领域/范围/是否启用 | 「系统有 tools、memory、mcp、skills、hooks、compaction 六项能力，其中 mcp 没启用」 |
| 状态呈现 | `product_surfaces.py` | **当前配置和运行状态**如何对外展示 | 「模型没配 base_url，MCP 连不上，所以 readiness=blocked」 |

一个管「能力」，一个管「状态」。前者是相对静态的登记（系统装配了哪些能力），后者是某一
时点的运行快照（这些能力此刻健康吗）。

**为什么两者要分开而不是合并**：能力目录是「结构」，状态是「观测」。合并成一个对象会让
「什么能力」和「能力是否健康」耦合——而「能力存在但故障」恰恰是最常见、最需要表达清楚
的状态（存在 ≠ 可用）。分开后，快照里只放 `capabilities` 这一列启用能力名，具体健康与否
由 `readiness` 单独表达。

## 裁剪：从参考项目到我们的方案（裁剪复现，非完整复现）

参考项目的 `capability_registry.py`（273 行）+ `product_surfaces.py`（464 行）是为它自己的
完整产品形态服务的，里面含三样我们**没做**的东西：`extension.json` 扩展系统、`model
fallback`（备用模型）、`background_tasks`（后台任务）。照搬的话，Day 25 会变成「先补一堆
Day 26+ 的依赖」，而不是做 Day 25 本身。逐项裁剪如下：

| 裁剪项 | 为什么裁 | 代价 | 什么信号再考虑 |
| --- | --- | --- | --- |
| 依赖图 / 标签索引 / 搜索 | 能力只有 6 个，不需要「大量能力的可发现性」 | 无依赖检查、无按标签筛选 | 能力涨到几十个、需要可发现性 |
| `@capability` 装饰器 | 靠 import 副作用自动注册，是隐式全局状态，违背 runtime_factory 的显式装配 | 写 `register()` 多几行 | 能力多到手动注册成负担 |
| 调用统计 / handler | Registry 是目录不是执行器；为统计搭执行入口 = 第二套真相 | 无调用次数 | 出现「诊断哪个能力没用」的真实场景 |
| `extension.json` | 我们扩展就是 skills + MCP，不等价通用插件 | `/extensions` 只展示 skills + MCP | 引入通用插件系统 |
| model fallback | 切换逻辑是 Day 27「路由」职责 | Readiness 只做配置就绪静态检查 | Day 27 |
| `background_tasks` | Day 26 才做 | 快照里 delegation 标「not wired」 | Day 26 |

**一个关键纠正**（对 GPT 看法的修正）：GPT 说 model fallback「最值得考虑」没错，但 fallback
的**切换逻辑**（什么错误触发、与重试的区别、切换后工具格式兼容）是 Day 27「路由/成本/验证
控制」的核心，不是 Day 25。Day 25 只做「模型配置就绪」的静态检查。所以这不是「裁掉
fallback」，是「没越界」——把切换留到它该在的那一天。

## 代码实现

### 1. `capability_registry.py` —— 纯元数据登记表

```python
@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    domain: str   # tool / memory / external / system / context
    scope: str    # workspace / external / system
    enabled: bool = True
    description: str = ""

class CapabilityRegistry:
    def register(self, capability): ...      # 重复 name 抛 ValueError
    def get(self, name) -> Capability | None
    def has(self, name) -> bool
    def list_enabled(self) -> list[str]      # 快照用
    def list_all(self) -> list[str]
```

**为什么是纯元数据、不存 handler、不做统计**：这是 GPT 指出的最关键一句——「如果只是登记
启用能力，不要为统计再搭一套执行入口」。执行已经在 `ToolRegistry`，`CapabilityRegistry`
再存 handler 就是「第二套执行路径、两个真相」，将来改一处漏一处。它只回答「有什么能力」，
不回答「怎么执行」。调用统计同理：要统计就必须接进 `ToolRegistry.execute` 的真实路径，而
我们现在没有「诊断哪个能力没用」的场景，为统计去改造执行路径是负收益，所以后置。

### 2. `product_surfaces.py` —— 状态汇总层

```python
@dataclass(frozen=True, slots=True)
class Readiness:
    status: Literal["ready", "degraded", "blocked"]
    reasons: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class InstructionLayer:      # name/scope/path/exists/preview（只存路径+预览，不存全文）
class ProductSnapshot:       # instructions/extensions/hooks/capabilities/readiness/delegation
    def to_dict(self) -> dict  # 全可序列化，不含密钥/进程/callback
```

**模型就绪判定 `_provider_ready`**（这里有个 subtle 点）：

```python
def _provider_ready(config) -> bool:
    if config.model == "mock":
        return True
    return bool(config.base_url and config.api_key)
```

为什么不用现成的 `validate_config`？因为 `validate_config` 只查「真实模型缺 api_key」和
「base_url scheme 不对」，**不查「真实模型缺 base_url」**——而 `build_adapter` 里真实模型缺
base_url 会直接 `ValueError`。所以 readiness 判定要补上 base_url 这一项，否则「有 api_key 但
没 base_url」会被误判成 ready，等真跑起来才崩。这是「就绪判定必须对齐真实执行路径的失败
条件」，不能只看校验函数。

### 3. `build_readiness` —— 三档判定（本日核心逻辑）

```python
def build_readiness(provider_ready, broken_extensions) -> Readiness:
    reasons = []
    if not provider_ready:
        reasons.append("provider unavailable (real model missing base_url or api_key)")
    reasons.extend(f"extension failed: {name}" for name in broken_extensions)
    if not provider_ready:
        status = "blocked"
    elif broken_extensions:
        status = "degraded"
    else:
        status = "ready"
    return Readiness(status, tuple(reasons))
```

三档的语义和边界：

| 档位 | 条件 | 语义 |
| --- | --- | --- |
| `blocked` | provider 不可用 | 核心闭环（模型→工具→结果）根本跑不起来，必须修 |
| `degraded` | provider 可用，但扩展失败 | 核心可用，只是部分扩展（如某 MCP）不可用 |
| `ready` | 两者都正常 | 全功能可用 |

**为什么单个扩展失败是 degraded 不是 blocked**：MCP server 连不上，本地只读工具、记忆、
压缩这些核心能力照跑，核心闭环不受影响。所以只「降级相关能力」，不「误杀整个系统」——
这正是课件验收「单个扩展失败只降低相关能力，不把整个系统误判为不可用」，也是课件易错点
「Readiness 不是所有可选模块全绿，而是当前目标能否执行」。

### 4. 装配（`runtime_factory.create`）

```python
registry = CapabilityRegistry()
registry.register(Capability("tools", "tool", "workspace", True, "builtin tool registry"))
registry.register(Capability("memory", "memory", "workspace", True, "project memory pipeline"))
registry.register(Capability("mcp", "external", "external", bool(mcp_result["tools"]), "MCP-backed tools"))
registry.register(Capability("skills", "tool", "workspace", bool(skills), "skill loading"))
registry.register(Capability("hooks", "system", "system", True, "hook event system"))
registry.register(Capability("compaction", "context", "workspace", True, "context compaction"))
snapshot = build_product_snapshot(cwd, registry, skills=skills, mcp_servers=..., hooks_stats=...)
```

注意 `mcp` 的 `enabled=bool(mcp_result["tools"])`、`skills` 的 `enabled=bool(skills)`——「能力
存在」和「能力启用」要区分：配置了 `.mcp.json` 但没连上任何工具，mcp 能力就是「登记但未
启用」，快照里不列它。`Runtime` 增加 `registry` + `snapshot` 字段。

### 5. CLI（`main.py`）

`/readiness` 从「只做配置校验」升级为读快照三档；新增 `/instructions`、`/extensions`。三个
命令统一读 `runtime.snapshot`，**不重复收集状态**：

```python
if t == "/readiness":
    r = runtime.snapshot.readiness
    lines = [f"readiness: {r.status}"]
    lines.extend(f"  - {reason}" for reason in r.reasons)
    lines.append(f"capabilities: {', '.join(runtime.snapshot.capabilities)}")
    return "\n".join(lines)
```

## 讲解

### 关键设计决策

1. **为什么 Registry 是目录不是执行器？** 执行已经有 `ToolRegistry`。Registry 再存 handler
   就是「两套执行路径、两个真相」。它的唯一职责是「自描述 + 启用登记」，供诊断和快照读。
2. **为什么快照的 capabilities 直接取自 registry？** 课件验收「Prompt 展示的启用能力与
   Registry 一致」。如果快照自己再算一遍能力列表，两处就可能不一致。单一来源最稳。
3. **为什么快照在启动时 build、不动态更新？** skills/MCP/配置都是启动时确定的，会话内不
   变；Readiness 主要看「配置就绪 + 扩展状态」，不是 Hook 热度。一次 build 足够，且满足
   「Session 能看到当时快照」的时点语义——快照本来就是「某个时点」的状态。
4. **为什么「能力存在」和「能力启用」要分两个概念？** 「配置了 MCP 但没连上」是「存在但
   未启用」，如果混成一个 `has_mcp` 布尔，就表达不了「配了但坏了」这个最常见的故障态。
   `enabled` 单独一维，`readiness.reasons` 再补「为什么没启用」。

### 易错点

- **Readiness 不是仪表盘全绿**（课件原话）：它回答「当前目标能否执行」，不是追求所有可选
  模块都打勾。MCP 挂了 degraded 就够，别因为一个可选扩展失败把系统判成 blocked。
- **快照必须可序列化**：不含密钥、进程对象、callback。`to_dict` 里只有 str/int/list/dict，
  这样 Session 才能安全地序列化保存、CLI 才能安全打印。
- **`/extensions` 只展示不管理**：不做安装/卸载（那是 Day 23 skills 的 install 职责），这里
  只读状态，不引入 `extension.json`。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 单个扩展失败只降低相关能力，不误判系统不可用 | ✅ degraded 测试 |
| Snapshot 不含密钥/进程对象/callback | ✅ `test_snapshot_has_no_secret` |
| Prompt 展示的启用能力与 Registry 一致 | ✅ `test_capabilities_match_registry` |
| Session inspect 能看到当时的产品快照 | ✅ `ProductSnapshot.to_dict()` 可序列化 |
| Readiness 三档 ready/degraded/blocked | ✅ `test_build_readiness` |
| 既有测试无回归 | ✅ 165 passed（原 157 + 新增 8） |
