# MiniCode 任务清单（TASKS）

> 可验收的开发单元，按里程碑组织。每个单元只有在「实现、接入主链路、验证、能脱稿讲清」四项都完成才算结束。

## 完成标准

每个 Day 的产出要同时满足：

1. 实现：对应模块代码存在且职责单一；
2. 接入：从入口能触发到该模块；
3. 验证：有测试或 Demo 证明行为正确；
4. 讲解：能脱稿讲清模块作用与位置。

## 里程碑

### V0 离线可解释 Agent（Day 1–8）

- [ ] 无 API Key 可运行
- [ ] Mock Model 能完成「列目录 → 读文件 → 总结」
- [ ] 未知工具、参数错误、工具异常都变成结构化结果，不击穿主循环
- [ ] 核心循环不依赖 TUI

### V0.1 可修代码的 Agent（Day 9–14）

- [ ] 一个真实 Provider 能调用工具
- [ ] 写入经过权限、Diff 和 Checkpoint
- [ ] 能「复现失败 → 修改 → 测试通过」
- [ ] 模型只说「完成」不能越过验证门槛

### V0.2 可长期使用的 Agent（Day 15–22）

- [ ] 进程重启后能恢复会话
- [ ] Replay 不重复副作用
- [ ] Rewind 不覆盖用户后续修改
- [ ] 极低上下文预算下仍保留目标、约束和验证状态
- [ ] TUI 与 Headless 复用同一业务运行时

### V1.0 可扩展、可评估的 Agent（Day 23–30）

- [ ] 禁用 Skills/Hooks/MCP/子 Agent 后，主链路仍可运行
- [ ] 扩展工具仍经过统一权限和工具结果协议
- [ ] 控制论增强层可单独关闭并做消融对比
- [ ] 干净环境安装后入口可用
- [ ] 质量报告明确标注通过、失败、跳过和外部阻塞

## 30 天任务

| Day | 交付物 | 验收命令（示意） |
| --- | --- | --- |
| 1 | 产品边界、四份文档、演示仓库 | `pytest tests/fixtures/buggy_calculator/tests -q`（预期 1 failed） |
| 2 | 包骨架、核心类型协议 | `python -m compileall -q minicode tests` |
| 3 | 配置合并、日志、安装入口 | `pytest tests/test_config.py -q` |
| 4 | Mock Model 与可脚本化响应 | `pytest tests/test_mock_model.py -q` |
| 5 | ToolRegistry 与工具生命周期 | `pytest tests/test_tools.py -q` |
| 6 | 工作区边界与只读工具 | `pytest tests/test_tools.py -q` |
| 7 | 最小 Agent Loop | `pytest tests/test_agent_loop.py -q` |
| 8 | 多工具、错误恢复、调度与指标 | `pytest tests/test_scheduler.py -q` |
| 9 | Prompt Pipeline 与产品上下文 | `pytest tests/test_prompt.py -q` |
| 10 | 真实 Provider 与重试/Fallback | `pytest tests/test_anthropic_adapter.py -q` |
| 11 | 权限模式与风险分类 | `pytest tests/test_permissions.py -q` |
| 12 | 写入、编辑、Diff、Checkpoint | `pytest tests/test_tools.py -q` |
| 13 | 命令执行、测试与后台进程 | `pytest tests/test_run_command_encoding.py -q` |
| 14 | Task/Turn 状态机与验证门槛 | `pytest tests/test_turn_kernel.py -q` |
| 15 | Session 保存、增量存储、Inspect | `pytest tests/test_session.py -q` |
| 16 | Replay、Resume、Rewind 与冲突 | `pytest tests/test_session.py -q` |
| 17 | Token 预算、工具输出预算、去重 | `pytest tests/test_context_compactor.py -q` |
| 18 | 自动/响应式压缩与工作记忆 | `pytest tests/test_context_compactor.py -q` |
| 19 | 项目记忆、检索、作用域隔离 | `pytest tests/test_memory_e2e.py -q` |
| 20 | 记忆流水线、重排、向量与时间线 | `pytest tests/test_memory_reranker.py -q` |
| 21 | CLI、Headless、历史、用户画像 | `pytest tests/test_main.py tests/test_headless.py -q` |
| 22 | TUI 输入、事件、渲染与 Transcript | `pytest tests/test_tui.py -q` |
| 23 | Skills 与 Hooks | `pytest tests/test_skills.py tests/test_hooks.py -q` |
| 24 | MCP 工具、资源、提示词与生命周期 | `pytest tests/test_mcp.py -q` |
| 25 | Product Surfaces 与能力注册 | `pytest tests/test_product_surfaces.py -q` |
| 26 | 后台任务、子 Agent、任务图 | `pytest tests/test_background_tasks.py -q` |
| 27 | 路由、成本、指标、验证控制器 | `pytest tests/test_progress_controller.py -q` |
| 28 | 反馈、前馈、预测、状态观测 | `pytest tests/test_advanced_cybernetics.py -q` |
| 29 | Orchestrator、自愈、稳定性、消融 | `pytest tests/test_cybernetic_orchestrator.py -q` |
| 30 | 全链路评估、CI、打包与发布演示 | `pytest tests/test_packaging.py tests/test_release_integration.py -q` |

> 验收命令为参考仓库测试的对应子集，具体是否复用参考测试见 DECISIONS.md（D7，待定）。
