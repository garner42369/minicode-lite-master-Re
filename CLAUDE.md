# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在本仓库中工作时提供指导。

## 项目目标

在本仓库（`minicode-lite`）中**完全复刻「初始版 minicode」**。

「初始版 minicode」指：

- `docs/` 下的两份课件：
  - `docs/minicode-30-day-vibe-coding/` —— 30 天复现课件（主课程、每日代码、模块地图、检查表、测试导航）
  - `docs/minicode-learning-courseware/` —— 学习课件（项目图谱、源码阅读路线、各子系统讲解）
- 参考项目 `D:\Project\MiniCode-Python`（一份完整的 Python 本地 AI Coding Agent Runtime）

复现方式：**不逐行照搬源码**。以课件为节奏和验收标准，参考源码只用于深度对照（自上而下看架构），用自己的结构实现，把坑踩一遍再修复，从而真正掌握。

## 什么是 minicode

minicode 是一个面向真实代码仓库的本地 AI Coding Agent Runtime（Python 3.11+，运行时零依赖、自研，不用 LangChain 等 Agent 框架）。核心闭环：模型提议 → 权限门 → 工具执行 → 结果回填 → 验证。

产品边界与架构见 `docs/PRODUCT_SPEC.md`、`docs/ARCHITECTURE.md`；有代价的取舍见 `docs/DECISIONS.md`。

## 技术栈

- 语言：Python 3.11+（本机 Anaconda Python 3.11.7）
- 运行时零依赖：`urllib.request` 直调 API、`subprocess` 执行命令、标准库实现 TUI
- 测试：pytest（dev 依赖）；property-based 测试需 `hypothesis`（本机未装，用到时再补）
- 包结构：`minicode/` 包 + `tests/`

## 协作方式

- **30 天逐日推进**：严格按 `docs/minicode-30-day-vibe-coding/` 的 30 天课程，每天完成一个单元后停下，等用户 review 并修完 bug 后再进下一天。
- **修正前先提醒**：执行中发现有意义的错误（运行时错误、测试失败、逻辑缺陷）时，先告知「问题 + 根因 + 修正方案」，再动手。
- **git 提交**：每天 commit，提交前给用户确认范围与信息。
- **交付物位置**：文档写 `docs/`，测试与 fixture 写项目内 `tests/`（fixture 在 `tests/fixtures/`）。
- **目录约定**：创建含 Python 文件的目录时，必须加空 `__init__.py`（配合相对导入）。

## 参考与边界

- 参考项目：`D:\Project\MiniCode-Python`（只读参考，**不要检查它的 git 记录**）。
- 验收标准（是否复用参考项目的 1164 个测试）尚未决定，到需要测试验证时再问用户。

## 仓库约定

- 默认分支：`master`
- 托管平台：Gitee（HTTPS），非 GitHub
- README 遵循 Gitee `Readme_XXX.md` 双语约定（中文为主）

## 语言

- 永远使用中文回答问题。术语/专业用语可用英文，但大部分沟通用简体中文。
