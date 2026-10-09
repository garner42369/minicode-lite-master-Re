"""minicode 的配置：不可变运行时配置 + 合并、校验、脱敏与加载。

配置优先级（越靠后越高）：
默认值 ← 用户级 settings ← 项目级 settings ← .env/环境变量 ← CLI 参数

Day 3 先落地不可变 RuntimeConfig 与纯函数合并/校验/脱敏；
真实 Provider 检测、模型 fallback、MCP scope 等留到 Day 10+ 再扩展。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """一次运行的有效配置。frozen + slots：不可变、内存紧凑、可哈希。"""

    model: str = "mock"          # 模型名；"mock" 表示离线 Mock（Day 4 实现）
    base_url: str = ""           # Provider API 地址；空表示用默认
    api_key: str = ""            # 凭据；展示/记录必须脱敏
    tool_profile: str = "core"   # 工具画像：core / full


def merge_config(base: RuntimeConfig, **overrides: object) -> RuntimeConfig:
    """把 overrides 合并进 base，返回新配置（纯函数，不改动入参）。

    只接受 RuntimeConfig 已知字段；None 或空串视为「未提供」，不覆盖 base。
    """
    allowed = {name for name in RuntimeConfig.__dataclass_fields__}
    clean = {k: v for k, v in overrides.items() if k in allowed and v not in (None, "")}
    return replace(base, **clean)


def validate_config(config: RuntimeConfig) -> list[str]:
    """校验配置，返回问题列表（空列表表示通过）。"""
    errors: list[str] = []
    # 真实模型必须有凭据；mock 不需要
    if config.model != "mock" and not config.api_key:
        errors.append("api_key is required for a real model")
    # base_url 若提供，必须是 http/https
    if config.base_url and urlparse(config.base_url).scheme not in {"http", "https"}:
        errors.append("base_url must use http or https")
    return errors


def safe_snapshot(config: RuntimeConfig) -> dict[str, str]:
    """返回可安全展示/记录的配置快照，密钥脱敏为末四位。"""
    masked = "not set" if not config.api_key else f"***{config.api_key[-4:]}"
    return {
        "model": config.model,
        "base_url": config.base_url,
        "api_key": masked,
        "tool_profile": config.tool_profile,
    }


def read_dotenv(path: str = ".env") -> dict[str, str]:
    """解析 .env 文件为键值对（纯函数，不写 os.environ，便于测试）。

    规则：忽略空行与 # 注释；每行 KEY=VALUE；值两端的引号会被剥掉。
    为什么自研：项目硬约束「运行时零依赖」，.env 解析极简，标准库即可。
    """
    result: dict[str, str] = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")  # 支持用引号包裹含空格的值
            if key:
                result[key] = value
    return result


def load_dotenv(path: str = ".env") -> None:
    """把 .env 文件合并进 os.environ（已存在的环境变量不覆盖）。

    设计：真实环境变量优先于 .env 文件——命令行/CI 注入的变量不应被文件覆盖。
    """
    for key, value in read_dotenv(path).items():
        if key not in os.environ:
            os.environ[key] = value


def load_runtime_config(env: dict[str, str] | None = None) -> RuntimeConfig:
    """从环境变量读取配置（先加载 .env，再读环境变量）。

    env 参数便于测试注入；默认先 load_dotenv() 合并 .env，再读 os.environ。
    """
    load_dotenv()
    env = env if env is not None else os.environ
    return RuntimeConfig(
        model=env.get("MINI_CODE_MODEL", "mock"),
        base_url=env.get("MINI_CODE_BASE_URL", ""),
        api_key=env.get("MINI_CODE_API_KEY", ""),
        tool_profile=env.get("MINI_CODE_TOOL_PROFILE", "core"),
    )
