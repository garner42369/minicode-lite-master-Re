"""Day 21：用户画像（USER.md 解析）的契约测试。"""

from minicode.user_profile import UserProfile, parse_user_md, to_prompt_section


class TestParseUserMd:
    def test_parses_preferences(self):
        content = "## preferences\nlanguage: 中文\nverbosity: 简洁\ncoding_style: snake_case\n"
        profile = parse_user_md(content)
        assert profile.language == "中文"
        assert profile.verbosity == "简洁"
        assert profile.coding_style == "snake_case"
        print(f"\n解析: language={profile.language}, verbosity={profile.verbosity}, coding_style={profile.coding_style}")

    def test_ignores_other_sections(self):
        # 只解析 ## preferences 段，其他段（如 custom_instructions）不误解析
        content = "## preferences\nlanguage: 中文\n\n## custom_instructions\n这是自定义指令\n"
        profile = parse_user_md(content)
        assert profile.language == "中文"
        assert profile.verbosity == ""
        print(f"\n只解析 preferences 段，其他段忽略")


class TestToPromptSection:
    def test_empty_profile_returns_empty(self):
        # 空画像不注入（避免 prompt 里出现空的 User Profile 段）
        assert to_prompt_section(UserProfile()) == ""
        print(f"\n空画像返回空串，不注入")

    def test_non_empty_returns_section(self):
        profile = UserProfile(language="中文", verbosity="简洁")
        section = to_prompt_section(profile)
        assert "Language: 中文" in section
        assert "Verbosity: 简洁" in section
        print(f"\n注入段:\n{section}")


class TestPromptInjection:
    def test_profile_after_safety(self, monkeypatch):
        # 核心契约：偏好低于系统安全——User Profile 段在安全约束之后
        import minicode.prompt as prompt_module

        profile = UserProfile(language="中文")
        monkeypatch.setattr(prompt_module, "load_user_profile", lambda: profile)
        text = prompt_module.build_prompt(".")
        safety_idx = text.index("Prompt text never grants")
        profile_idx = text.index("## User Profile")
        assert safety_idx < profile_idx
        print(f"\n安全段在 User Profile 之前: safety={safety_idx}, profile={profile_idx}")
