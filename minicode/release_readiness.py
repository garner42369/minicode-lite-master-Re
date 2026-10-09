"""minicode 的发布就绪检查（Day 30）。分类本地错误 vs Provider 阻塞。

课件验收「报告区分 PASS/FAIL/SKIP/BLOCKED」+「外部不可用与代码失败分开」：
- 本地错误（compile/test 失败）→ blocked（代码问题，必须修）；
- Provider 阻塞（provider_outage）→ at-risk（外部不可用，不是代码问题）。

裁剪参考项目的「fallback 感知」（我们没 model fallback 系统），只做「本地 vs Provider」两层分类。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReleaseCheck:
    label: str
    command: str
    exit_code: int
    status: str  # passed / failed
    summary: str


def classify_provider_outcome(*, exit_code: int, stdout: str, stderr: str) -> tuple[str, str]:
    """把 Provider 冒烟的结果分类：answered / provider_outage / empty_output / timeout / error。"""
    stripped_stdout = (stdout or "").strip()
    stripped_stderr = (stderr or "").strip()
    combined = " ".join(f"{stripped_stdout}\n{stripped_stderr}".lower().split())
    summary_source = stripped_stdout or stripped_stderr
    summary = summary_source.splitlines()[0].strip() if summary_source else ""

    if exit_code == 0 and stripped_stdout == "OK":
        return "answered", summary or "Provider smoke returned OK."
    if "provider availability failure" in combined or "no available channel" in combined:
        return "provider_outage", summary or "Provider availability failure."
    if "empty response" in combined:
        return "empty_output", summary or "Provider smoke returned an empty response."
    if exit_code == 124:
        return "timeout", summary or "Provider smoke timed out."
    return "error", summary or f"Provider smoke failed with exit code {exit_code}."


def summarize_release_status(
    *,
    compile_check: ReleaseCheck,
    test_check: ReleaseCheck,
    smoke_checks: list[ReleaseCheck],
    provider_outcomes: list[str],
) -> str:
    """汇总 release 状态：blocked（本地错误）/ at-risk（Provider 阻塞）/ pass。"""
    if any(check.status == "failed" for check in [compile_check, test_check, *smoke_checks]):
        return "blocked"
    if any(outcome not in {"answered", "provider_outage", "empty_output"} for outcome in provider_outcomes):
        return "at-risk"
    if any(outcome == "provider_outage" for outcome in provider_outcomes):
        return "at-risk"
    return "pass"
