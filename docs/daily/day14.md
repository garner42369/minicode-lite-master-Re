# Day 14：Turn Kernel、任务对象与验证门槛——由运行时决定何时结束

## 今日任务

模型可能在没读文件、没测试、甚至工具失败后说「完成」。今天把任务目标、阶段、预算、验证
状态和停止原因从 Prompt 暗示升级为显式状态——不是模型说结束就结束，而是运行时根据证据决定。

## 任务分析

前 13 天的 Loop 里，模型返回 `<final>` 就直接结束。这有个漏洞：模型可以改完代码（甚至没验证）
就宣称「完成」。Day 14 引入 Turn Kernel，把「能不能结束」从模型的自由发挥，变成运行时的
显式判断。

## 代码实现

### 1. TurnState + decide_final —— 验证证据 + 终止判断

```python
class TurnState:
    changed_version: str = ""      # 最近修改的文件版本（内容哈希）
    verified_version: str = ""     # 最近验证通过的文件版本
    step: int = 0

    def record_change(self, version):          # 改了文件：设置 changed，清除 verified
        self.changed_version = version
        self.verified_version = ""             # 证据失效——验证结论不再覆盖新内容

    def record_verification(self, version, passed):  # 跑了测试：passed 才设 verified，失败清空证据
        if passed:
            self.verified_version = version
            self.last_verification_failed = False
        else:
            self.verified_version = ""              # 验证失败，旧证据失效（避免 evidence_ready 误判）
            self.last_verification_failed = True

    def evidence_ready(self):                  # 验证证据是否覆盖当前文件版本
        return bool(self.changed_version) and self.changed_version == self.verified_version


def decide_final(state, requires_verification):
    if state.step >= state.max_steps:
        return Decision(True, "max_steps")                    # 预算耗尽，先停
    if requires_verification:
        if state.last_verification_failed:
            return Decision(False, "verification_failed", "验证失败，修复后重跑")
        if not state.evidence_ready():
            return Decision(False, "verification_missing", "运行验证后再结束")
    return Decision(True, "done")                             # 证据够了，放行
```

**关键点**：

- `changed_version`/`verified_version` 都是文件内容哈希，`record_change` 清除 `verified_version`
  ——验证后一旦再改文件，证据立即失效；
- `decide_final` 在模型 final 时被调，根据「改了没、验了没」判断能不能真的结束。

## 讲解

### 每个模块为什么存在

| 模块 | 回答的问题 | 谁消费 |
| --- | --- | --- |
| `turn_kernel.py` | 当前阶段、能否结束 | Loop |
| `intent_parser.py` | 用户想做哪类动作（SEARCH/CODE/DEBUG/REFACTOR） | Kernel |
| `task_object.py` | 本轮稳定目标、约束 | Kernel |
| `runtime_profiles.py` | single/single-deep 的预算策略 | Kernel |
| `verification_controller.py` | 需无验证/目标测试/全量验证 | Kernel |
| `task_tracker.py` | 多步骤待办进展 | Kernel、后续 TUI（不进 Day 18 压缩） |

### 数据流

```
模型返回 final
        │
        ▼
decide_final(state, requires_verify)
        ├── 预算耗尽 → max_steps
        ├── 需要验证 且 验证失败 → verification_failed（nudge 模型去修）
        ├── 需要验证 且 证据不足（改了没验）→ verification_missing（nudge 模型去跑测试）
        └── 否则 → done（放行）
        │
        ▼
不满足 → 注入 nudge 到历史，模型继续；满足 → 真正结束
```

### 关键设计决策

1. **为什么 Kernel 决策与 Loop 执行分离？** Loop 负责「执行」，Kernel 负责「决策」。若把「什么
   时候能结束」散落在 Loop 的 if 分支里，它会和「怎么执行」纠缠在一起，既难测试、也难随策略
   变化。分离后 `decide_final` 成为独立可测的纯函数，Loop 只负责执行和服从决策。

2. **为什么验证证据绑定文件版本？** `changed_version` 与 `verified_version` 都是文件内容哈希，
   验证后一旦再改文件，证据立即失效。因为测试结论是针对「被验证的那个文件内容」的；验证通过
   后又改了一行代码，旧结论就不再覆盖新内容。不做这个失效，模型就能「先跑测试、再偷偷改代码、
   仍宣称验证通过」。

3. **为什么 requires_verification 随意图变化？** 只读解释任务（SEARCH）没有副作用，强制它跑
   pytest 是浪费；改代码的任务（CODE/DEBUG/REFACTOR）至少要目标测试。验证策略随风险变化，
   不是一刀切。

4. **为什么「该不该改」和「改没改」是两个不同的问题？** 意图（intent）回答「该不该改代码」，
   `changed_version` 回答「改没改代码」。验证门槛要区分三种情况：只读任务（不该改、也没改）→
   不验证；修改任务（该改、也改了）→ 验证；修改任务（该改、但没改，模型偷懒）→ 该拦截。如果
   只用 `changed_version`，第三种情况会被误判成「只读任务、不验证」，放走一个「没完成任务就
   宣布完成」的模型。

## Kernel 在 Agent Runtime 里的位置

Turn Kernel 是「**决策层**」，夹在 Loop 的「执行」和「结束」之间，是整个 runtime「由证据决定
何时结束」的核心：

- **下游消费 Day 13 的证据**：`test_runner` 跑出的「测试通过」触发 `record_verification`，让
  `verified_version` 追上 `changed_version`——Day 13 的「验证」是 Day 14「验证门槛」的输入；
- **意图（intent）决定策略**：`intent_parser` 分出的 SEARCH/CODE/DEBUG/REFACTOR，决定
  `requires_verify` 和预算策略（`runtime_profiles` 的 single/single-deep）；
- **稳定任务事实供 Day 18 压缩用**：`task_object`（目标/约束）组成 Stable Task Pack 的原料，
  Day 18 压缩上下文时「受保护」不被摘要丢掉。`task_tracker`（任务图进度）留给后续 TUI 阶段，
  不进 Day 18 的 Stable Task Pack。

所以 Day 14 是「从能用走向可靠」的分水岭——它把「模型说完成」这个不可靠的信号，替换成了
「运行时根据证据判断能不能结束」这个可靠机制。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 提前 final 被拦截并产生 nudge | ✅ 剧本 1：edit_file 后 final 被拦 |
| 文档解释任务不被强制 pytest | ✅ 剧本 2：SEARCH 直接 done |
| 验证后再修改清除 evidence-ready | ✅ turn_kernel_demo 场景 3 |
| max_steps / verification_failed / verification_missing / done 明确映射 | ✅ decide_final 覆盖 |
| Kernel 接回 Agent Loop（task 可选，不破坏既有行为） | ✅ 72 passed |

## 小结

Day 14 是整个项目从「能用」到「可靠」的分水岭。前 13 天，模型返回 `<final>` 就直接结束；
Day 14 引入 Turn Kernel，把「什么时候能结束」从模型的自由发挥，变成运行时的显式决策——**
不是模型说结束就结束，而是运行时根据证据决定**。核心是一套清晰的职责分工：Loop 负责
「执行」，Kernel 负责「决策」；意图回答「该不该改」，版本哈希回答「改没改、验没验」。这套
分工让「提前 final 被拦截」「验证后再改失效」「该改没改被拦」这些原本「模型自觉」的事，
变成了「运行时强制」的事。V0.1 里程碑的意义在于：到这里核心闭环已经完整打通，接下来不是
急着堆「高级感」功能，而是先用真实任务检验可靠性。
