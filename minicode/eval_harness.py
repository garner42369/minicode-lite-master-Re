"""minicode 的评估 harness（Day 30）。每个场景使用全新 fixture，结果区分 PASS/FAIL/SKIP/BLOCKED。

每个场景：复制 fixture 到临时目录 → run 修改 workspace → verify 检查期望 → 判定。
核心原则（课件「每次任务重置 fixture，前次改动不污染后次结果」）：copy 到全新临时目录，
绝不原地跑，否则上一个场景的改动会污染下一个。
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Callable


class Status(str, Enum):
    PASS = "pass"        # 期望达成
    FAIL = "fail"        # 期望未达成 / 运行异常
    SKIP = "skip"        # 主动跳过（如缺依赖）
    BLOCKED = "blocked"  # 被权限/安全拦住（本身是正确行为）


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    fixture: Path
    run: Callable[[Path], None]       # 在 workspace 上执行（修改文件等）
    verify: Callable[[Path], bool]    # 检查 workspace 是否符合期望


@dataclass(frozen=True, slots=True)
class EvalRow:
    name: str
    status: Status
    duration_ms: float
    evidence: str


@dataclass(slots=True)
class Harness:
    rows: list[EvalRow] = field(default_factory=list)

    def evaluate(self, scenario: Scenario) -> EvalRow:
        started = perf_counter()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "fixture"
            shutil.copytree(scenario.fixture, workspace)
            try:
                scenario.run(workspace)
                passed = scenario.verify(workspace)
                row = EvalRow(
                    scenario.name,
                    Status.PASS if passed else Status.FAIL,
                    (perf_counter() - started) * 1000,
                    "workspace expectation evaluated",
                )
            except PermissionError as exc:
                # 被权限/安全拦住——对「拒绝编辑/路径逃逸」这类场景，这是正确行为
                row = EvalRow(scenario.name, Status.BLOCKED, (perf_counter() - started) * 1000, str(exc))
            except Exception as exc:  # noqa: BLE001
                row = EvalRow(scenario.name, Status.FAIL, (perf_counter() - started) * 1000, f"{type(exc).__name__}: {exc}")
        self.rows.append(row)
        return row

    def summary(self) -> dict[str, int]:
        return {status.value: sum(row.status is status for row in self.rows) for status in Status}
