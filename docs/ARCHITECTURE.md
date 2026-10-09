# MiniCode 架构说明（ARCHITECTURE）

> Day 1 冻结分层与依赖方向。重点不是列文件，而是回答：每层负责什么、谁依赖谁、什么依赖是禁止的。

## 总体架构

```text
用户输入 / CLI / Headless / TUI
        |
        v
配置加载 + Prompt 构建 + 模型适配 + 工具注册 + 权限初始化   （装配层）
        |
        v
Agent 主循环 run_agent_turn
        |
        +--> 模型推理: Anthropic / OpenAI-compatible / Mock
        +--> 工具调用: read / grep / write / edit / run_command / test ...（scheduler 分组并发/串行 + 错误恢复）
        +--> 安全治理: 路径权限、命令守卫、编辑 diff、checkpoint
        +--> 上下文治理: token 估算、工具预算、去重、压缩、恢复
        +--> 记忆治理: user/project/local 记忆、检索、注入、反思
        +--> 会话治理: transcript、checkpoint、replay、rewind
        |
        v
最终回复 + 文件结果 + 运行事件 + 会话持久化
```

## 分层与职责

| 层 | 职责 | 不做什么 |
| --- | --- | --- |
| 入口层（main/headless） | 装配配置、模型、工具、权限、prompt | 不实现推理逻辑 |
| Agent 核心（agent_loop/scheduler/turn_kernel） | 编排模型—工具交替，调度工具执行，维护阶段与停止原因 | 不直接读写文件 |
| 工具层（tooling/tools/） | 执行真实动作，返回统一 ToolResult | 不决定「任务是否完成」 |
| 权限层（permissions/file_review/workspace） | 决定动作是否被授权 | 不负责实际执行 |
| 模型层（model_registry/adapters） | 翻译 Provider 协议为统一 AgentStep | 不决定权限、不写文件 |
| 上下文/记忆 | 决定哪些信息进入下一次模型请求 | 不执行业务 |
| 会话 | 保存可恢复状态 | 回放不能重放副作用 |
| 扩展/评估 | MCP/Skills/Hooks、harness 与 readiness | 作为可关闭扩展接入 |

## 依赖方向不变量（必须遵守）

1. **工具不调用 UI**：工具只通过 ToolResult / 事件回传，不直接碰 TUI。
2. **Provider 不写文件**：模型适配器只返回 AgentStep，文件变更必须走工具 + 权限 + checkpoint。
3. **主循环不直接读写文件**：Loop 通过工具执行动作。
4. **权限只做裁决，不做执行**：权限判定 allow/deny，实际读写由工具完成。
5. **TUI / Headless 是同一运行时的两个消费者**，不是两套业务逻辑。

## 核心协议（系统共同语言）

Day 2 起落地，Day 1 先冻结协议边界：

- `ChatMessage`：对话消息及其角色。
- `ToolCall`：一次工具调用，必须有稳定 ID，用于与结果配对。
- `AgentStep`：模型一步的产出——最终文本、进度文本，或工具请求。
- `RuntimeEvent`：运行时向 UI 发出的结构化事件。
- `ModelAdapter`：模型适配器协议（`next(messages) -> AgentStep`），Provider 差异在此收敛。

工具侧：

- `ToolDefinition` / `ToolMetadata` / `ToolContext` / `ToolResult` / `ToolRegistry`。

关键约束：

- Provider 的原始 JSON 不离开 Adapter。
- 进度与最终回答必须可区分（否则 Loop 会在进度播报后错误结束）。

## 停止原因（运行时层，Day 14 细化）

产品级四种停止结果（见 PRODUCT_SPEC）之外，运行时还会记录更细的停止原因：
`done` / `max_steps` / `await_user` / `blocked` / `verification_failed` / `widen_needed`。
