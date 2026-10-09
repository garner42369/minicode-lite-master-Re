"""Day 9：Prompt Pipeline 的契约测试。

覆盖四类契约：
1. 边界标记分隔静态前缀与动态后缀；
2. Section 缓存（TTL 命中 / 条件跳过 / 显式失效）；
3. 文件缓存（mtime 未变不重读，mtime 变则重读）；
4. build_prompt 组装（动态注入、malformed 容错、指令来源与优先级）。
"""

import os
import pathlib
from pathlib import Path

from minicode.prompt import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    PromptPipeline,
    PromptSection,
    build_prompt,
    content_hash,
    read_file_cached,
)


class TestPromptSection:
    def test_ttl_caches_builder(self):
        # 缓存未过期时 builder 不重算
        calls: list[int] = []

        def builder() -> str:
            calls.append(1)
            return "v"

        section = PromptSection("x", builder, cache_ttl=100.0)
        assert section.evaluate() == "v"
        assert section.evaluate() == "v"
        assert len(calls) == 1  # 第二次命中缓存

    def test_condition_skips_section(self):
        pipeline = PromptPipeline()
        pipeline.register_dynamic("skip", lambda: "SKIP_ME", condition=lambda: False)
        pipeline.register_dynamic("keep", lambda: "KEEP_ME", condition=lambda: True)
        result = pipeline.build()
        assert "SKIP_ME" not in result
        assert "KEEP_ME" in result

    def test_clear_cache_forces_rebuild(self):
        calls: list[int] = []
        pipeline = PromptPipeline()
        pipeline.register_dynamic("x", lambda: (calls.append(1), f"v{len(calls)}")[1], cache_ttl=100.0)
        first = pipeline.build()
        second = pipeline.build()
        assert first == second and len(calls) == 1  # 缓存命中
        pipeline.clear_cache()
        pipeline.build()
        assert len(calls) == 2  # clear 后重算


class TestReadFileCached:
    def test_skips_reread_when_mtime_unchanged(self, tmp_path: Path, monkeypatch):
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        assert read_file_cached(f) == "hello"

        calls: list[int] = []
        real_read_text = pathlib.Path.read_text

        def spy(self, *args, **kwargs):
            calls.append(1)
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "read_text", spy)
        assert read_file_cached(f) == "hello"
        assert calls == []  # mtime 未变，命中缓存，不读盘

    def test_rereads_when_mtime_changes(self, tmp_path: Path):
        f = tmp_path / "a.txt"
        f.write_text("v1", encoding="utf-8")
        assert read_file_cached(f) == "v1"

        f.write_text("v2", encoding="utf-8")
        new_mtime = f.stat().st_mtime + 10
        os.utime(f, (new_mtime, new_mtime))
        assert read_file_cached(f) == "v2"  # mtime 变 → 失效重读

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert read_file_cached(tmp_path / "nope.md") is None


class TestBuildPrompt:
    def test_boundary_separates_static_and_dynamic(self):
        prompt = build_prompt("/ws", permissions=["read workspace"])
        assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY in prompt
        static, dynamic = prompt.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY, 1)
        assert "You are minicode" in static
        assert "cwd=/ws" in dynamic
        print(f"\n=== 完整 Prompt ===\n{prompt}")

    def test_static_hash_stable_across_dynamic_change(self):
        p1 = build_prompt("/ws", skills=["a"])
        p2 = build_prompt("/ws", skills=["a", "b"])
        static1 = p1.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)[0]
        static2 = p2.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)[0]
        assert content_hash(static1) == content_hash(static2)  # 静态区稳定
        assert p1 != p2  # 动态区变化

    def test_injects_cwd_and_permissions(self):
        prompt = build_prompt("/workspace", permissions=["read workspace", "run tests"])
        assert "cwd=/workspace" in prompt
        assert "read workspace" in prompt
        assert "run tests" in prompt

    def test_dynamic_sections_only_when_present(self):
        empty = build_prompt("/ws")
        assert "Available skills" not in empty
        assert "Configured MCP servers" not in empty
        assert "Project Memory" not in empty

        full = build_prompt("/ws", skills=["testing"], mcp_servers=[{"name": "m"}], memory="remember")
        assert "Available skills" in full
        assert "Configured MCP servers" in full
        assert "Project Memory" in full

    def test_malformed_entries_produce_diagnostics(self):
        # 坏 skill/MCP 条目只产生诊断，不阻止整体构建
        prompt = build_prompt(
            "/ws",
            skills=["ok", {"name": "s", "description": "d"}, 12345],
            mcp_servers=[{"name": "m1", "status": "connected", "toolCount": 2}, "bad"],
        )
        assert "malformed skill entry" in prompt
        assert "malformed MCP server entry" in prompt
        assert "ok" in prompt  # 好条目不受影响

    def test_instructions_annotate_source_and_precedence(self, tmp_path: Path, monkeypatch):
        # 用 tmp_path 同时充当 cwd 与 home，完全隔离真实的 ~/.claude/CLAUDE.md
        (tmp_path / "CLAUDE.md").write_text("project rule", encoding="utf-8")
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("global rule", encoding="utf-8")
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))

        prompt = build_prompt(str(tmp_path))
        assert "Project instructions" in prompt
        assert "Global instructions" in prompt
        assert "project rule" in prompt and "global rule" in prompt
        # 项目指令在全局指令之前（优先级）
        assert prompt.index("Project instructions") < prompt.index("Global instructions")
        print(f"\n=== 指令注入 ===\n{prompt}")
