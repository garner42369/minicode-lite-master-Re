"""minicode 的日志：初始化、子 logger、关键路径事件 helper 与密钥脱敏。

设计原则：
- 日志只记录「事实」，不吞异常、不记录完整密钥。
- 分级 + 控制台/文件（按大小轮转），避免日志无限增长。
- 关键路径日志点：API 调用、工具执行、权限检查、会话事件。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

# 日志目录与文件
LOG_DIR = Path.home() / ".mini-code"
LOG_FILE = LOG_DIR / "minicode.log"

# 轮转配置：按大小轮转，防止日志无限增长
LOG_MAX_BYTES = 10 * 1024 * 1024   # 单文件 10 MB
LOG_BACKUP_COUNT = 5               # 保留 5 个轮转文件

CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d: %(message)s"


def setup_logging(
    level: str = "INFO",  # 控制台默认 INFO：显示关键节点不刷屏；文件 handler 始终 DEBUG
    log_to_file: bool = True,
    log_to_console: bool = True,
) -> logging.Logger:
    """初始化 minicode 日志系统，返回根 logger。

    先清除已有 handler，避免重复调用时日志重复输出。
    """
    if log_to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("minicode")
    root.setLevel(getattr(logging, level.upper(), logging.WARNING))
    root.handlers.clear()

    file_formatter = logging.Formatter(FILE_FORMAT)
    console_formatter = logging.Formatter(CONSOLE_FORMAT)

    if log_to_file:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别
        file_handler.setFormatter(file_formatter)
        root.addHandler(file_handler)

    if log_to_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(getattr(logging, level.upper(), logging.WARNING))
        console_handler.setFormatter(console_formatter)
        root.addHandler(console_handler)

    # 降低第三方库日志噪音
    for noisy in ("urllib3", "httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root.info(
        "Logging initialized (level=%s, file=%s, console=%s)",
        level, log_to_file, log_to_console,
    )
    return root


def get_logger(name: str) -> logging.Logger:
    """获取子模块 logger，如 get_logger("tools") -> minicode.tools。"""
    return logging.getLogger(f"minicode.{name}")


def log_tool_execution(tool_name: str, success: bool, duration_ms: float, error: str | None = None) -> None:
    """记录工具执行（Day 5 的 ToolRegistry 会调用）。"""
    logger = get_logger("tools")
    if success:
        logger.debug("Tool %s executed successfully in %dms", tool_name, duration_ms)
    else:
        logger.warning("Tool %s failed after %dms: %s", tool_name, duration_ms, error)


def log_api_call(model: str, tokens_in: int, tokens_out: int, cost: float, duration_ms: float) -> None:
    """记录 API 调用。注意：只记录模型与用量，绝不记录密钥。"""
    logger = get_logger("api")
    logger.info(
        "API call: model=%s, tokens_in=%d, tokens_out=%d, cost=$%.4f, duration=%dms",
        model, tokens_in, tokens_out, cost, duration_ms,
    )


def log_permission_check(
    kind: str,
    target: str,
    verdict: str,
    risk: str = "",
    feedback: str = "",
) -> None:
    """记录权限决策（脱敏）。Day 11 的 PermissionManager 会调用。

    脱敏要点：
    - 权限日志本身不涉及密钥，但 feedback 是用户/模型给的原因，可能含敏感
      内容，只记录前 80 字符的截断版本；
    - 拒绝是重要安全事件，用 warning 级别；允许是正常流程，用 debug 级别。
    """
    logger = get_logger("permissions")
    safe_feedback = feedback if len(feedback) <= 80 else feedback[:80] + "..."
    if verdict.startswith("deny"):
        logger.warning(
            "Permission %s: kind=%s, target=%s, risk=%s%s",
            verdict,
            kind,
            target,
            risk,
            f", feedback={safe_feedback!r}" if safe_feedback else "",
        )
    else:
        logger.debug(
            "Permission %s: kind=%s, target=%s, risk=%s",
            verdict,
            kind,
            target,
            risk,
        )


def log_session_event(event: str, details: str = "") -> None:
    """记录会话事件（Day 15 的 Session 会调用）。"""
    logger = get_logger("session")
    if details:
        logger.info("Session %s: %s", event, details)
    else:
        logger.info("Session %s", event)
