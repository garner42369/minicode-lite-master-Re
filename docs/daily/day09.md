# Day 9：Prompt Pipeline——系统提示词是一份运行时产品快照

## 今日任务

实现 `PromptPipeline`，把系统提示词拆成「静态前缀 + 动态后缀」两部分，用边界标记隔开。

系统提示词不是一段固定文字，而是告诉模型 cwd、权限、工具、Skills、MCP、记忆和运行规则——
一份「运行时产品快照」。若写成一个巨型字符串，会导致缓存失效、难测试、动态信息过期。

## 任务分析

**为什么拆静态 / 动态？** 三个问题各有出处：

1. **缓存失效**：提示词里混着每轮变化的 cwd、当前 token 用量，Provider 无法跨轮复用缓存；
2. **难测试**：无法断言「动态内容没有混进静态区」；
3. **信息过期**：高频状态塞进提示词会制造陈旧信息。

**边界标记是给谁的？** 不是给用户看的，而是帮 Provider 做 Prompt Cache（静态前缀可跨轮/跨
会话缓存），也让测试能验证动态内容没混进静态区。

## 代码实现

### 1. PromptSection + PromptPipeline —— 静态/动态分离 + 缓存

```python
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"   # 静态/动态的边界标记

@dataclass
class PromptSection:                    # 一段声明式提示词
    name: str
    builder: Callable[[], str]          # 产出文本的函数
    condition: Callable[[], bool] | None = None   # 条件不满足就跳过
    cache_ttl: float = 300.0            # 缓存 TTL

    def evaluate(self) -> str | None:
        if self.condition is not None and not self.condition():
            return None                                    # 条件不满足 → 跳过
        if self._cached_value is not None and (now - self._cached_at) < self.cache_ttl:
            return self._cached_value                      # 命中缓存 → 不重算
        self._cached_value = self.builder()                # 重算
        return self._cached_value

class PromptPipeline:
    def build(self):
        parts = [s.evaluate() for s in self._static]           # 静态前缀
        parts.append(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)           # 边界标记
        parts.extend(s.evaluate() for s in self._dynamic)      # 动态后缀
        return "\n\n".join(p for p in parts if p)
```

**关键点**：

- `PromptSection.evaluate()` 三层逻辑：条件不满足跳过 → 命中缓存返回 → 否则 `builder()` 重算。
  这是「静态段跨轮缓存、动态段每轮重建」的实现；
- `build()` 把静态段、边界标记、动态段拼起来，边界标记 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`
  是 Provider 做 Prompt Cache 的切分点。

### 2. build_prompt —— 组装具体内容

```python
def build_prompt(cwd, *, permissions=None, skills=None, mcp_servers=None, memory=""):
    pipeline = PromptPipeline()
    pipeline.register_static("role", _STATIC_ROLE)          # 角色 + 工具使用规则
    pipeline.register_static("safety", _STATIC_SAFETY)      # 安全约束（Prompt 不是授权）
    pipeline.register_static("protocol", _STATIC_PROTOCOL)  # <progress>/<final> 协议
    # 动态段：cwd + 权限摘要 + skills + mcp + memory + 指令文件（每轮重建）
    ...
    return pipeline.build()
```

**关键点**：静态三段（role/safety/protocol）几乎不变、跨轮缓存；动态段（cwd、权限、skills、
mcp、memory、指令文件）每轮按需重建，且「有内容才注入」。

## 讲解

### 每个模块为什么存在

| 能力 | 位置 | 谁消费 |
| --- | --- | --- |
| `PromptSection` | prompt.py | Pipeline 内部（一段提示词的缓存/条件） |
| `PromptPipeline` | prompt.py | build_prompt（组装静态+动态） |
| `build_prompt` | prompt.py | Day 10 adapter（作为 system message） |

### 数据流

```
build_prompt(cwd, permissions, skills, mcp, memory)
        │
        ├── 静态段（role / safety / protocol）—— 跨轮缓存，几乎不变
        ├── 边界标记 SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        └── 动态段（cwd / 权限 / skills / mcp / memory / 指令文件）—— 每轮重建
        │
        ▼
一段完整的 system prompt ──► Day 10 adapter 作为 messages[0] 发给 Provider
```

### 关键设计决策

1. **为什么「Prompt 不是授权」（本次红线）？** 提示词里写「不要访问工作区外」只是**引导**，
   不是**授权**——真正的授权必须发生在 `PermissionManager`（Day 11）和工具执行路径中。因为
   提示词是对模型的「请求」，模型可能遵守也可能不遵守；权限层是对动作的「强制」。一个恶意
   的 CLAUDE.md 即使声称「忽略权限」，也不能改变执行策略。这条红线直接决定 Day 11 的设计。

2. **为什么静态内容不塞 cwd？** cwd 是会话相关的、每次运行可能不同，放静态会污染本应跨轮
   复用的缓存前缀。凡是「会话级、可能变」的，都该在边界标记之后（动态区）。这样 Provider
   才能把「静态前缀」稳定地缓存。

3. **为什么 malformed 动态条目只诊断、不失败？** 动态 section 来自外部（Skills/MCP 配置），
   单个坏条目不该让整个 Prompt 构建失败——否则一个写坏的 skill 清单会让 Agent 连启动都做不到。
   容错边界放在「最外层组装」，坏条目降级为诊断文本。

4. **为什么指令文件「项目优先、全局次之」？** 先读项目 CLAUDE.md 再读全局 CLAUDE.md，项目
   指令排在前面。因为项目级指令更贴近当前任务，应覆盖全局默认；同时每条指令标注来源路径，
   模型和调试者都能追溯「这条规则是谁定的」。

## Prompt 在 Agent Runtime 里的位置

Prompt Pipeline 是「**模型输入**」的组装器——它把「当前运行时该告诉模型什么」组装成一段
system prompt。它的位置决定了它和几个 Day 的关系：

- **Day 10 的 adapter**：真实 Provider 发请求时，用 `build_prompt(cwd)` 生成的 system prompt
  作为 messages 的第一条——「模型看到的运行时快照」就来自这里；
- **Day 11 的权限**：Prompt 里可以「描述」权限（告诉模型哪些能做），但真正的授权在权限层
  ——这条边界 Day 9 就划清了；
- **Day 17 的上下文预算**：静态/动态分离的思想，和上下文预算「不变的东西缓存、变的东西
  重建」是同一个思路。

所以 Prompt Pipeline 的价值不只是「组装一段文字」，更是确立了「**系统提示词 = 运行时快照**」
这个认知——它是一份「当前 cwd、权限、工具、记忆」的实时描述，而不是一段写死的模板。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 动态能力变化后 Prompt 更新（静态区不变） | ✅ `test_static_hash_stable_across_dynamic_change` |
| 边界标记分隔静态前缀与动态后缀 | ✅ `test_boundary_separates_static_and_dynamic` |
| 缓存未过期时 builder 不重算 | ✅ `test_ttl_caches_builder` |
| 文件 mtime 变则失效重读 | ✅ `test_rereads_when_mtime_changes` |
| 坏 skill/MCP 项只诊断、不阻止构建 | ✅ `test_malformed_entries_produce_diagnostics` |
| 指令文件标注来源 + 项目优先 | ✅ `test_instructions_annotate_source_and_precedence` |
| 全部测试通过 | ✅ `72 passed` |

## 小结

Day 9 的核心是「系统提示词是一份运行时产品快照」，不是一段固定文字。把提示词拆成「静态
前缀（跨轮缓存）+ 动态后缀（每轮重建）」，既解决了缓存失效、又让动态信息能及时更新。更重要
的是确立了一条红线：**Prompt 不是授权**——提示词里的「不要访问工作区外」只是引导，真正的
授权在 Day 11 的权限层。这条红线会直接影响 Day 11 的设计，是整个安全模型的地基之一。
