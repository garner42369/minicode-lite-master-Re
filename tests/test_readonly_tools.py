"""Day 6：工作区路径边界与只读工具的契约测试。"""

from pathlib import Path

import pytest

from minicode.tooling import ToolContext
from minicode.tools import create_core_tool_registry
from minicode.workspace import resolve_workspace_path


class TestWorkspacePath:
    def test_resolve_relative_path(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        resolved = resolve_workspace_path(ToolContext(cwd=str(tmp_path)), "a.txt")
        assert resolved == (tmp_path / "a.txt").resolve()

    def test_resolve_rejects_parent_traversal(self, tmp_path: Path):
        # 核心：../ 不能逃出工作区
        with pytest.raises(PermissionError):
            resolve_workspace_path(ToolContext(cwd=str(tmp_path)), "../outside.txt")

    def test_resolve_rejects_absolute_external_path(self, tmp_path: Path):
        external = tmp_path.parent / "other.txt"
        with pytest.raises(PermissionError):
            resolve_workspace_path(ToolContext(cwd=str(tmp_path)), str(external))


class TestReadonlyTools:
    def test_list_files_lists_directory(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        registry = create_core_tool_registry()
        result = registry.execute("list_files", {"path": "."}, ToolContext(cwd=str(tmp_path)))
        assert result.ok is True
        assert "a.txt" in result.output
        assert "sub" in result.output

    def test_read_file_returns_content_with_header(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hello\nworld", encoding="utf-8")
        registry = create_core_tool_registry()
        result = registry.execute("read_file", {"path": "a.txt"}, ToolContext(cwd=str(tmp_path)))
        assert result.ok is True
        assert "hello" in result.output
        assert "TOTAL=" in result.output

    def test_read_file_pagination(self, tmp_path: Path):
        content = "line\n" * 100
        (tmp_path / "big.txt").write_text(content, encoding="utf-8")
        registry = create_core_tool_registry()
        result = registry.execute(
            "read_file", {"path": "big.txt", "offset": 0, "limit": 10}, ToolContext(cwd=str(tmp_path))
        )
        assert result.ok is True
        assert "TRUNCATED=True" in result.output
        print("\n=== read_file 分页输出 ===\n" + result.output)

    def test_read_file_not_found(self, tmp_path: Path):
        registry = create_core_tool_registry()
        result = registry.execute("read_file", {"path": "missing.txt"}, ToolContext(cwd=str(tmp_path)))
        assert result.ok is False
        assert result.error_code == "not_found"

    def test_read_file_binary(self, tmp_path: Path):
        (tmp_path / "img.bin").write_bytes(b"\x00\x01\x02\xff")
        registry = create_core_tool_registry()
        result = registry.execute("read_file", {"path": "img.bin"}, ToolContext(cwd=str(tmp_path)))
        assert result.ok is False
        assert result.error_code == "binary_file"

    def test_read_file_chinese(self, tmp_path: Path):
        (tmp_path / "中文.txt").write_text("中文内容\n第二行", encoding="utf-8")
        registry = create_core_tool_registry()
        result = registry.execute("read_file", {"path": "中文.txt"}, ToolContext(cwd=str(tmp_path)))
        assert result.ok is True
        assert "中文内容" in result.output
        print("\n=== read_file 中文输出 ===\n" + result.output)

    def test_grep_finds_matches(self, tmp_path: Path):
        (tmp_path / "calc.py").write_text("def divide(a, b):\n    return a // b\n", encoding="utf-8")
        registry = create_core_tool_registry()
        result = registry.execute(
            "grep_files", {"pattern": "divide", "path": "."}, ToolContext(cwd=str(tmp_path))
        )
        assert result.ok is True
        assert "calc.py:1" in result.output  # 相对路径:行号
        print("\n=== grep 结果 ===\n" + result.output)

    def test_grep_no_match(self, tmp_path: Path):
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        registry = create_core_tool_registry()
        result = registry.execute(
            "grep_files", {"pattern": "zzz_nothing", "path": "."}, ToolContext(cwd=str(tmp_path))
        )
        assert result.ok is True
        assert "(no matches)" in result.output
