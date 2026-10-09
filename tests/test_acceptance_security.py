"""验收测试：安全边界（危险命令分类 + 路径逃逸）。

模拟真实场景里「模型试图越界」的情况，暴露权限/安全层的缺口。
"""

from pathlib import Path

import pytest

from minicode.auto_mode import classify_command
from minicode.permissions import RiskLevel
from minicode.tooling import ToolContext
from minicode.workspace import resolve_workspace_path


class TestDangerousCommandClassification:
    def test_rm_rf_is_dangerous(self):
        risk, _ = classify_command("rm", ["-rf", "build"])
        assert risk is RiskLevel.DANGEROUS

    def test_git_reset_hard_is_dangerous(self):
        # 参考项目把 git reset --hard 列为「丢弃本地更改」的危险命令
        risk, reason = classify_command("git", ["reset", "--hard"])
        assert risk in {RiskLevel.HIGH, RiskLevel.DANGEROUS}, (
            f"git reset --hard 应判危险，实际 {risk.value}（{reason}）"
        )

    def test_git_push_force_is_dangerous(self):
        risk, _ = classify_command("git", ["push", "--force"])
        assert risk in {RiskLevel.HIGH, RiskLevel.DANGEROUS}

    def test_chmod_777_is_dangerous(self):
        risk, _ = classify_command("chmod", ["777", "file"])
        assert risk in {RiskLevel.HIGH, RiskLevel.DANGEROUS}

    def test_mkfs_is_dangerous(self):
        risk, _ = classify_command("mkfs", [])
        assert risk is RiskLevel.DANGEROUS

    def test_sudo_is_high(self):
        risk, _ = classify_command("sudo", ["rm", "-rf", "/"])
        assert risk in {RiskLevel.HIGH, RiskLevel.DANGEROUS}

    def test_read_only_command_is_safe(self):
        risk, _ = classify_command("ls", ["-la"])
        assert risk is RiskLevel.SAFE


class TestPathEscape:
    def test_parent_traversal_denied(self, tmp_path):
        ctx = ToolContext(cwd=str(tmp_path))
        with pytest.raises(PermissionError):
            resolve_workspace_path(ctx, "../outside.txt")

    def test_absolute_external_denied(self, tmp_path):
        ctx = ToolContext(cwd=str(tmp_path))
        with pytest.raises(PermissionError):
            resolve_workspace_path(ctx, str(tmp_path.parent / "other" / "x.txt"))

    def test_inside_workspace_allowed(self, tmp_path):
        ctx = ToolContext(cwd=str(tmp_path))
        path = resolve_workspace_path(ctx, "src/main.py")
        assert path == (tmp_path / "src" / "main.py").resolve()
