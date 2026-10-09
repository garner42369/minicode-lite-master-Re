# Day 30：Harness、CI、打包与最终复现——用证据结束课程

## 今日任务

最后一天不再加功能，而是证明系统**能安装、能完成固定任务、失败能分类**，并整理成可维护
的产品。

## 做了什么

### 1. `eval_harness.py` — 评估框架（课件对齐）

```python
class Status(Enum): PASS / FAIL / SKIP / BLOCKED

@dataclass
class Scenario:
    name / fixture / run / verify   # run 修改 workspace，verify 检查期望

class Harness:
    def evaluate(scenario):
        # 复制 fixture 到全新临时目录 → run → verify → 判定
```

核心原则（课件「每次任务重置 fixture，前次改动不污染后次结果」）：**copy 到全新临时目录**，
绝不原地跑。`PermissionError` 记 BLOCKED（对「拒绝编辑/路径逃逸」场景，这是正确行为）。

### 2. `release_readiness.py` — 发布就绪（本地 vs Provider）

- `classify_provider_outcome`：把 Provider 冒烟结果分类 `answered / provider_outage /
  empty_output / timeout / error`。
- `summarize_release_status`：**本地错误（compile/test 失败）→ blocked**；**Provider 阻塞
  （outage）→ at-risk**；否则 pass。课件「外部不可用与代码失败分开」。

### 3. `tests/v04_fix_buggy_harness.py` — 真实 Provider 修复（端到端冒烟）

复制 `buggy_calculator` 到临时目录 → 真实模型修复 → pytest 验证 2 passed。**任务描述刻意
模糊**：

```text
"这个项目有一个失败的测试。请运行测试、查看失败原因，定位并修复代码里的缺陷，然后重新运行测试确认全部通过。"
```

不告诉模型「divide 用了 `//` 应该改成 `/`」——否则是「照指令改」，不是「真实修复」。模型
必须自己跑测试、看失败、定位 bug、修复。这是「真实 Provider 冒烟」：证明系统能完成一个
真实修复任务（不只是单元测试里 mock 出来的行为）。

**踩坑（编码）**：`subprocess.run(capture_output=True, text=True)` 用系统默认 GBK 解码
subprocess 输出，而 pytest 输出含 UTF-8 中文（calculator.py 的中文 docstring），GBK 解码
失败导致 `_readerthread` 抛 `UnicodeDecodeError`、stdout 为 None。修复：显式
`encoding="utf-8", errors="replace"`。这是 Windows 上「subprocess 捕获子进程输出」的经典坑。

### 4. README 更新 + 打包 wheel

- README 从 Gitee 模板更新成实际的安装/配置/使用/扩展/测试说明（课件验收「README 命令与
  实际入口一致」）。
- `python -m pip wheel . --no-deps -w dist` 构建成功：`minicode-0.1.0-py3-none-any.whl`。

## 三层验证

| 层 | 是否默认运行 | 证明什么 |
| --- | --- | --- |
| 单元/离线集成 | 是 | 协议、状态机和失败分支确定正确（214 个测试） |
| 临时工作区端到端 | 是 | 真实文件/进程/Session 能协作（eval_harness 复制 fixture） |
| 真实 Provider 冒烟 | 有凭据时 | 外部 API 当前可用，能完成真实修复（v04） |

## 关键设计决策

1. **真实修复的任务描述要模糊**：不透露 bug 位置和改法，让模型自己定位——否则「真实修复」
   退化成「照指令改」，验证不了模型的理解能力。
2. **冒烟是「证明能完成真实任务」，不是「替代离线正确性」**：离线测试（214 个）是确定性
   正确性的主证据；真实冒烟只证明「外部 API 可用 + 端到端能跑通」。
3. **打包验证「能装能用」**：构建 wheel 成功，证明 pyproject.toml 的声明正确（零运行时依赖、
   入口 `minicode-py` / `minicode-headless` 可解析）。

## 30 天收官

从 Day 1 的产品契约，到 Day 30 的打包结项，`minicode` 完成了一个本地 AI Coding Agent
Runtime 的完整复现：

- **214 个测试**全部通过（协议/工具/权限/记忆/MCP/子 Agent/控制论各层契约）；
- 真实模型端到端跑通（记忆召回 v02、子 Agent 委派 v03、真实修复 v04）；
- 打包可安装（wheel 构建成功，零运行时依赖）；
- 25 条 DECISIONS 记录了所有有代价的取舍（砍 TUI、Hook 只 continue、裁剪关键词堆砌、
  控制论「了解意义为主」、消融证明「阈值够用」……）。

## 验收对照

| 验收项 | 结果 |
| --- | --- |
| 默认离线检查可在无密钥机器运行 | ✅ 214 个测试零网络依赖 |
| 测试依赖从项目配置完整安装 | ✅ dev = ["pytest"]，未用 hypothesis |
| 每个真实任务重新复制 fixture | ✅ eval_harness 复制到临时目录 |
| 报告区分 PASS/FAIL/SKIP/BLOCKED | ✅ eval_harness Status + 测试 |
| README 的命令与实际入口一致 | ✅ minicode-py / minicode-headless 已声明 |
| 真实 Provider 修复 buggy_calculator | ✅ v04 harness（需凭据手动跑，已修复编码） |
| 构建 wheel | ✅ minicode-0.1.0-py3-none-any.whl |
| 既有测试无回归 | ✅ 214 passed（原 204 + 新增 10） |
