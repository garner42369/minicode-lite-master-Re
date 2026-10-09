# Day 29：Orchestrator、自愈与消融——把增强层接入，同时保持可拆除

## 今日任务

把 Day 27/28 的控制器通过一个 Orchestrator 接进 loop，用**消融**回答核心问题——「自动调整
有没有让任务更稳定、更省资源、且不损害质量」。

## 做了什么

### 1. `control_orchestrator.py` — 控制编排器（课件对齐）

```python
@dataclass
class ControlOrchestrator:
    enabled: bool = True
    max_healings: int = 2        # 有界自愈：同一故障不无上限重复 healing
    healing_count: int = 0
    history: list[ControlAction]  # 审计：记录每个动作

    def after_tool(self, signals) -> list[ControlAction]:
        # context_ratio >= 0.90 → compact
        # tool_error_rate >= 0.5  → reduce_concurrency
        # repeated_failures >= 2 且 healing_count < max_healings → retry_idempotent_request
```

三个信号（上下文压力 / 工具错误率 / 重复失败）→ 三个动作（压缩 / 减并发 / 有界重试）。**可
关闭**（enabled=False 零副作用）、**有界**（max_healings 防无限自愈）、**可审计**（history）。

裁剪参考项目 cybernetic_orchestrator 的 15 控制器 orchestration——那些控制器（卡尔曼/RGA/
正反馈）Day 28 已经裁了，Day 29 只接「真实用得上」的三个信号。

### 2. `ablation.py` — 消融（合成画像，不调真实模型）

```python
def run_ablation(profiles) -> AblationReport:
    # 每个画像跑两臂：
    #   baseline（阈值 ThresholdController）vs cybernetic（PID PIDContextController）
    # 比溢出次数 / 压缩次数 / 最终占用
```

复用 Day 28 的 `simulate`，合成画像（增长序列）对比两个策略。

### 3. 接线 agent_loop（最小接线）

`run_agent_turn` 加 `orchestrator` 参数：工具执行后调 `orchestrator.after_tool(signals)`，
`compact` 动作设置 `force_compact = True`，下一轮发请求前**强制压缩**（不等 usage 超阈值）。

---

## 消融结果：阈值够用，PID 默认关闭

三个合成画像（mild / moderate / stress）跑下来：

```text
baseline（阈值）: overflows=0.0, compactions=4.67, final_usage=0.70
cybernetic（PID）: overflows=0.0, compactions=4.67, final_usage=0.70
delta:             overflows=0.0, compactions=0.0,  final_usage=0.0
```

**delta 全 0**——阈值和 PID 在「溢出、压缩次数、最终占用」三个维度上**完全一样**。

这个结论的价值在于：**它诚实地证明「阈值够用，PID 默认关闭」，而不是「PID 更好」**。PID
在合成画像下没有带来任何额外收益，却增加了调参（kp/ki/kd）和解释成本。这正是课件易错点
反复强调的：「若一个阈值规则已足够稳定，PID 可能只增加调参和解释成本」。

## 回答核心问题

**自动调整有没有让任务更稳定、更省资源、且不损害质量？**

- Day 29 的合成画像消融给出的答案是：**在合成画像下，PID 相比阈值没有额外收益（delta 全 0）**。
  也就是说，简单阈值已经够用，PID 这类「更高级的自动调整」在合成场景下是「不必要」的。
- 但这**不是最终答案**——合成画像不是真实任务。真实任务上的消融（比成功率/工具错误率/步数/
  Token/费用）由 **Day 30 的固定任务 Harness** 补上（13 个固定任务里的「控制论 baseline/full
  消融」）。

所以 Day 29 的诚实定位是：**把增强层接进 loop（可拆除），用合成消融给出「阈值够用」的初步
证据，真实任务验证留给 Day 30**。

## 关键设计决策

1. **消融先合成画像、后真实任务**：合成画像快、确定性、可单测，先回答「方向对不对」（PID 有
   没有额外收益）；真实任务贵，Day 30 用固定任务验证。
2. **Orchestrator 可拆除**：`enabled=False` 零副作用，核心 Loop 不依赖它（参数默认 None）。
   课件「完全移除/屏蔽 cybernetic 模块时核心包仍可运行」。
3. **自愈有界**：`max_healings` 限制同一故障的 healing 次数，不无限重试（课件「同一故障不会
   无上限重复 healing」）。
4. **接线最小**：只接 `compact` 动作（触发压缩，复用已有 actuator），`reduce_concurrency` /
   `retry` 记录到 history、不深入改 scheduler/重试逻辑——避免「为接线而接线」。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 完全移除/屏蔽控制模块时核心包仍可运行 | ✅ orchestrator 参数默认 None，enabled=False 零副作用 |
| 自愈不会无上限重复 healing | ✅ max_healings 有界（测试） |
| Supervisor 报告包含信号、动作、理由 | ✅ history 审计 + ControlAction.reason |
| 消融报告能指出收益与代价 | ✅ delta 全 0 → 「阈值够用，PID 不必要」 |
| 控制器级消融确定性 | ✅ 相同画像 → 相同 delta（测试） |
| 既有测试无回归 | ✅ 204 passed（原 198 + 新增 6） |
