"""Day 10 真实模型演示：用 DeepSeek 跑完整 Agent Loop，直接打印全过程。

运行（项目根目录）：
    python tests/real_model_demo.py

需要 .env 里有真实凭据。本脚本只 print、不 assert，目的是「亲眼看到」
真实 Provider 如何驱动模型 → 工具 → 模型的完整闭环。
"""

import sys
from pathlib import Path

# 把项目根加入 sys.path：命令行 `python tests/xxx.py` 时 sys.path[0] 是 tests/
# 而非项目根，不加会找不到 minicode 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 控制台默认 GBK，打印中文前先切到 UTF-8，避免乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from minicode.agent_loop import run_agent_turn
from minicode.config import load_dotenv, load_runtime_config
from minicode.cost_tracker import CostTracker
from minicode.model_registry import build_adapter
from minicode.prompt import build_prompt
from minicode.tools import create_core_tool_registry

# 项目根目录：脚本在 tests/ 下，向上两级即是项目根
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    # 用绝对路径加载 .env（无论从哪个目录运行都能读到凭据）
    load_dotenv(str(PROJECT_ROOT / ".env"))
    config = load_runtime_config()

    masked = f"***{config.api_key[-4:]}" if config.api_key else "not set"
    print("=== 配置 ===")
    print(f"  model    : {config.model}")
    print(f"  base_url : {config.base_url}")
    print(f"  api_key  : {masked}")

    tools = create_core_tool_registry()
    tracker = CostTracker()
    adapter = build_adapter(config, tools=tools.list(), cost_tracker=tracker)

    cwd = str(PROJECT_ROOT)
    system_prompt = build_prompt(cwd)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请用 list_files 查看项目根目录，再用 read_file 读 README.md，然后用一两句话解释这个项目是做什么的。"},
    ]

    print("\n=== 开始真实 Agent Loop ===")
    history, stop_reason = run_agent_turn(
        adapter,
        tools,
        messages,
        cwd,
        max_steps=8,
        on_tool_start=lambda c: print(f"\n  [工具] 调用 {c.name}({c.arguments})"),
        on_tool_result=lambda c, r: print(f"  [工具] {c.name} -> ok={r.ok}\n         输出: {r.output[:200]!r}"),
        on_assistant_message=lambda s: print(
            f"\n  [模型] {s.type}/{s.kind}"
            f"\n         思考链: {(s.reasoning or '')[:150]!r}"
            f"\n         正文: {(s.content or '')[:200]!r}"
        ),
    )

    print("\n=== 结果 ===")
    print(f"  stop_reason : {stop_reason}")
    print(f"  最终回答    : {history[-1]['content']}")
    totals = tracker.totals()
    print(f"  成本        : 调用 {totals.calls} 次, "
          f"输入 {totals.input_tokens} tokens, "
          f"输出 {totals.output_tokens} tokens, "
          f"费用 ${totals.cost_usd:.6f}")


if __name__ == "__main__":
    main()
