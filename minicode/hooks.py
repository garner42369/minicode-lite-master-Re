"""minicode 的 Hooks 事件系统（Day 23）。

Hook 是「运行时事件上的确定性自动化」——日志、格式化、通知、策略检查，在稳定的
事件点上自动执行。与 Skill 不同：Skill 是模型按需读取的工作流说明，Hook 是确定性
代码在事件点自动执行，二者分开，不合并成一个「万能插件管理器」。

策略（用户拍板）：对齐参考项目 hooks.py，hook 错误只隔离（continue）、不阻断主流程
——不做超时、不做 block / fail-closed。安全预检等「必须阻断」的场景留待未来需要时
再扩展 FailurePolicy。

关键设计：fire_sync 的 **data 是可变字典，handler 可往里面写结果**（如 AGENT_START
的记忆注入文本），fire 调用方通过传入的可变容器（dict）读回。这让「有返回值的 hook」
（记忆注入）不必改变 handler 签名，仍保持 Callable[[HookContext], None]。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class HookEvent(str, Enum):
    """生命周期 hook 事件（对齐参考项目 12 事件；Day 23 只接线其中 4 个）。"""

    PRE_TOOL_USE = "pre_tool_use"       # 工具执行前
    POST_TOOL_USE = "post_tool_use"     # 工具执行后
    AGENT_START = "agent_start"         # 一轮 agent turn 开始（发请求前）
    AGENT_STOP = "agent_stop"           # 一轮 agent turn 结束（含所有停止原因）
    SUBAGENT_START = "subagent_start"   # 子 Agent 启动（Day 26）
    SUBAGENT_STOP = "subagent_stop"     # 子 Agent 结束（Day 26）
    SESSION_SAVE = "session_save"       # 会话自动保存
    SESSION_RESUME = "session_resume"   # 会话恢复
    USER_INPUT = "user_input"           # 用户提交输入
    ASSISTANT_OUTPUT = "assistant_output"  # 助手产生回复
    STARTUP = "startup"                 # 应用启动
    SHUTDOWN = "shutdown"               # 应用关闭


@dataclass
class HookContext:
    """传给 hook handler 的上下文。data 是可变字典，handler 可写入结果供 fire 调用方读。"""

    event: HookEvent
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class HookRegistration:
    """一条已注册 hook 的元信息（含统计，供诊断）。"""

    event: HookEvent
    handler: Callable[[HookContext], None]
    description: str = ""
    enabled: bool = True
    call_count: int = 0
    failure_count: int = 0
    last_error: str = ""
    last_status: str = "idle"
    total_duration_ms: int = 0


class HookManager:
    """Hook 注册与执行。

    fire_sync 同步执行，异常只隔离、不向上抛（continue 策略）；一个坏 hook 不会让
    其余 hook 消失，错误文本通过返回值可见（满足课件「坏 Hook 诊断可见」验收）。
    """

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookRegistration]] = {e: [] for e in HookEvent}
        self._enabled = True

    def register(
        self,
        event: HookEvent,
        handler: Callable[[HookContext], None],
        description: str = "",
    ) -> Callable[[], None]:
        """注册一个 hook，返回 unregister 函数（供测试/运行时反注册）。"""
        reg = HookRegistration(event=event, handler=handler, description=description)
        self._hooks[event].append(reg)

        def unregister() -> None:
            if reg in self._hooks[event]:
                self._hooks[event].remove(reg)

        return unregister

    def fire_sync(self, event: HookEvent, **data: Any) -> list[str]:
        """同步 fire 一个事件，返回失败 hook 的错误文本列表（全部成功则空列表）。

        用 list() 快照再遍历，避免 handler 在回调里反注册/新注册导致迭代中途改变。
        """
        if not self._enabled:
            return []
        context = HookContext(event=event, data=data)
        errors: list[str] = []
        for reg in list(self._hooks[event]):
            if not reg.enabled:
                continue
            start = time.time()
            try:
                reg.handler(context)
                reg.last_status = "success"
                reg.last_error = ""
            except Exception as exc:  # noqa: BLE001 —— hook 是外部/不受信内容，必须兜底
                reg.failure_count += 1
                reg.last_status = "error"
                reg.last_error = str(exc)
                name = reg.description or getattr(reg.handler, "__name__", "hook")
                errors.append(f"{name}: {exc}")
            finally:
                reg.call_count += 1
                reg.total_duration_ms += int((time.time() - start) * 1000)
        return errors

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def get_stats(self, event: HookEvent | None = None) -> dict[str, Any]:
        """汇总统计：注册数 / 启用数 / 调用数 / 失败数 / 各 hook 明细。"""
        if event is not None:
            regs = list(self._hooks[event])
        else:
            regs = [r for rs in self._hooks.values() for r in rs]
        return {
            "total_hooks": len(regs),
            "enabled_hooks": sum(1 for r in regs if r.enabled),
            "total_calls": sum(r.call_count for r in regs),
            "total_duration_ms": sum(r.total_duration_ms for r in regs),
            "failure_count": sum(r.failure_count for r in regs),
            "hooks": [
                {
                    "event": r.event.value,
                    "description": r.description or getattr(r.handler, "__name__", "hook"),
                    "enabled": r.enabled,
                    "call_count": r.call_count,
                    "failure_count": r.failure_count,
                    "last_status": r.last_status,
                    "last_error": r.last_error,
                    "total_duration_ms": r.total_duration_ms,
                }
                for r in regs
            ],
        }


def create_logging_hook(target: list[str] | Path | None = None) -> Callable[[HookContext], None]:
    """内置日志 hook：把每个事件写进内存列表或追加到文件（课件 demo 的 PRE_TOOL_USE 日志）。

    target 为 list 时往列表 append（测试用）；为 Path 时追加写文件。
    """
    def handler(ctx: HookContext) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(ctx.timestamp))
        msg = f"[{ts}] {ctx.event.value}"
        if "tool_name" in ctx.data:
            msg += f" tool={ctx.data['tool_name']}"
        if target is None:
            return
        if isinstance(target, list):
            target.append(msg)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

    return handler
