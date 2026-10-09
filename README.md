# minicode —— 本地 AI Coding Agent Runtime

一个面向真实代码仓库的本地 AI Coding Agent Runtime（Python 3.11+）。**运行时零依赖、自研**，
不引入 LangChain / httpx 等第三方框架：`urllib.request` 直调模型 API、`subprocess` 执行命令、
标准库实现交互式 CLI。

核心闭环：

```text
模型提议 → 权限门 → 工具执行 → 结果回填 → 验证
```

## 核心能力

按复现顺序分层：

| 阶段 | 能力 |
| --- | --- |
| 基础能力 | 产品契约、协议、配置、Mock 模型、工具注册、只读工具、Agent 主循环、调度器、Prompt 管线、Provider 适配、权限门、受控写工具、命令执行 |
| 核心能力 | Turn Kernel（阶段/预算/验证证据）、Session、Safe Rewind、上下文预算、压缩与工作记忆、项目记忆（BM25）、记忆流水线 |
| 交互入口 | 交互式 CLI、无头 Headless（稳定退出码）、用户画像（USER.md） |
| 扩展生态 | Skills（按需加载）、Hooks（事件自动化）、MCP（stdio 客户端 + 双协议）、Product Surfaces、Capability Registry、后台任务、子 Agent（并发委派）、任务图 |
| 自适应运行时 | 指标、模型路由、成本/验证控制、控制论（PID）、Orchestrator、消融 |

## 安装

```bash
# 开发安装（含 pytest）
pip install -e ".[dev]"
```

- 运行时依赖：**零**（全部自研标准库实现）。
- 开发依赖：仅 `pytest`（`dev` extra）。

## 配置

复制 `.env.example` 为 `.env`，填入真实模型：

```bash
# 模型名（deepseek-v4-pro 旗舰推理 / deepseek-v4-flash 快速低成本）
MINI_CODE_MODEL=deepseek-v4-pro
# Provider API 地址（OpenAI 兼容协议）
MINI_CODE_BASE_URL=https://api.deepseek.com
# API 密钥（日志与快照自动脱敏）
MINI_CODE_API_KEY=sk-xxxx
# 工具画像：core（默认）/ full
MINI_CODE_TOOL_PROFILE=core
```

`MINI_CODE_MODEL` 为 `mock`（默认）时离线运行，无密钥也能体验完整闭环。

## 使用

### 交互式 CLI

```bash
python -m minicode.main --cwd /path/to/repo
```

本地命令（不进模型）：

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示帮助 |
| `/model` | 显示当前模型 |
| `/readiness` | 就绪检查（ready/degraded/blocked） |
| `/instructions` | 列出指令层 |
| `/extensions` | 列出扩展（Skills + MCP） |
| `/memory` | 列出项目记忆 |
| `/sessions` | 列出会话 |
| `/exit` | 退出 |

其他输入作为用户任务进入模型。副作用操作会走交互式审批。

### Headless（面向 CI / 脚本）

```bash
python -m minicode.headless "修复失败的测试" --cwd /path/to/repo --allow-edits
```

参数：

| 参数 | 作用 |
| --- | --- |
| `--cwd` | 工作目录 |
| `--allow-edits` | 显式允许本次运行的副作用编辑（默认拒绝） |
| `--json` | 以 JSON 输出结构化结果 |
| `--trace` | 工具调用 trace 到 stderr |

**稳定退出码**（机器调用者靠它判断成败，不靠文本）：

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功（stop_reason == done） |
| 1 | 运行时错误（任务未完成 / 异常） |
| 2 | 配置错误（如 base_url 缺失） |
| 3 | 未授权（权限层拒绝副作用导致任务无法继续） |

### 安装后直接调用

```bash
minicode-py                             # 交互式 CLI
minicode-headless "任务" --allow-edits   # Headless
```

## 扩展

- **Skills**：目录 + `SKILL.md`，四层搜索根（project / user / compat_project / compat_user），
  模型按需 `load_skill` 加载全文。
- **MCP**：项目级 `.mcp.json` 配置（默认不信任，需 `"enabled": true`），stdio 双协议
  （content-length / newline-json）auto-detect。
- **Hooks**：`AGENT_START` / `AGENT_STOP` / `PRE_TOOL_USE` / `POST_TOOL_USE` 等事件。
- **用户画像**：全局 `~/.mini-code/USER.md`，注入语言/解释深度/代码风格偏好（低于系统安全）。

## 测试

```bash
python -m pytest -q
```

真实模型集成 harness（手动跑，需要 `.env` 凭据）：

| harness | 验证 |
| --- | --- |
| `tests/v02_memory_harness.py` | 跨会话记忆召回 |
| `tests/v03_subagent_harness.py` | 子 Agent 并发委派 |
| `tests/v04_fix_buggy_harness.py` | 真实模型修复失败测试（端到端） |

## 目录结构

```
minicode/   —— 运行时包（零依赖）
tests/      —— 单元测试 + 真实模型 harness（v01~v04）+ fixtures
docs/       —— 产品规格 / 架构 / 取舍（DECISIONS）/ 30 天每日讲解
```

## 30 天复现

完整复现过程见 `docs/minicode-30-day-vibe-coding/`（主课程 + 每日代码）与
`docs/daily/dayNN.md`（每日讲解，含踩坑记录）。参考项目 `MiniCode-Python` 仅作深度对照。
