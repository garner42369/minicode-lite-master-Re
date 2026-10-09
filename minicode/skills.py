"""minicode 的 Skill 系统（Day 23）。

Skill 是「供模型按需读取的工作流说明」：一个目录 + 一份 SKILL.md。发现阶段只读
摘要（name + description），全文只在模型调用 load_skill 时按需加载——避免把所有
Skill 全文塞进 prompt（既撑爆上下文，又让不相关的 Skill 干扰模型）。

Skill 是指令来源，不是新权限：Skill 内容里要求「写入文件 / 执行命令」时，仍要走
权限门（课件 Day 23「Skill 内容不能绕过权限」）。

搜索根四层，优先级 project > user > compat_project > compat_user（用户拍板「四根
含 .claude 兼容」）：先到先得，同名 skill 高优先级根覆盖低优先级。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SkillSummary:
    """一个 skill 的摘要（发现阶段产物，不含全文）。"""

    name: str
    description: str
    path: str
    source: str


@dataclass(slots=True)
class LoadedSkill(SkillSummary):
    """加载全文后的 skill（在摘要基础上加 content）。"""

    content: str


def extract_description(markdown: str) -> str:
    """从 SKILL.md 提取描述：只认 frontmatter 的 `description:` 前缀（课件 daily_code）。

    不兼容无 frontmatter 的 SKILL.md——找不到 description 返回空串，不回退到「正文首段」。
    """
    for line in markdown.replace("\r\n", "\n").splitlines():
        if line.startswith("description:"):
            return line.removeprefix("description:").strip()
    return ""


def _home_dir() -> Path:
    return Path.home()


def _skill_roots(cwd: str | Path) -> list[tuple[Path, str]]:
    """四层搜索根，列表顺序即优先级（发现时先到先得）。"""
    base = Path(cwd)
    home = _home_dir()
    return [
        (base / ".mini-code" / "skills", "project"),
        (home / ".mini-code" / "skills", "user"),
        (base / ".claude" / "skills", "compat_project"),
        (home / ".claude" / "skills", "compat_user"),
    ]


def _read_skill_file(path: Path) -> str | None:
    """读 SKILL.md 文本；不可读（OSError）返回 None，由调用方跳过。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def discover_skills(cwd: str | Path) -> list[SkillSummary]:
    """发现所有 skill，只读摘要（name + description），不读全文。

    用 setdefault 实现「先到先得」：高优先级根先遍历，同名 skill 只保留第一个命中。
    """
    by_name: dict[str, tuple[str, str, str]] = {}  # name -> (description, path, source)
    for root, source in _skill_roots(cwd):
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue  # Windows 下不受信挂载点 / 断链，跳过
            skill_path = entry / "SKILL.md"
            if not skill_path.is_file():
                continue
            content = _read_skill_file(skill_path)
            if content is None:
                continue
            by_name.setdefault(entry.name, (extract_description(content), str(skill_path), source))
    return [
        SkillSummary(name, description, path, src)
        for name, (description, path, src) in by_name.items()
    ]


def load_skill(cwd: str | Path, name: str) -> LoadedSkill | None:
    """按名字加载 skill 全文；不存在或不可读返回 None。"""
    normalized = name.strip()
    if not normalized:
        return None
    for root, source in _skill_roots(cwd):
        skill_path = root / normalized / "SKILL.md"
        if not skill_path.is_file():
            continue
        content = _read_skill_file(skill_path)
        if content is None:
            return None
        return LoadedSkill(normalized, extract_description(content), str(skill_path), source, content)
    return None


def _managed_skill_root(scope: str, cwd: str | Path) -> Path:
    """托管目录：安装/删除只操作这里，不碰 source（课件「只操作托管目录」）。"""
    if scope == "project":
        return Path(cwd) / ".mini-code" / "skills"
    return _home_dir() / ".mini-code" / "skills"


def install_skill(
    cwd: str | Path,
    source_path: str,
    name: str | None = None,
    scope: str = "user",
) -> dict[str, str]:
    """把一份 SKILL.md（目录或文件）装进托管目录，返回 name 与目标路径。"""
    source = Path(source_path)
    if not source.is_absolute():
        source = Path(cwd) / source
    if source.is_dir():
        skill_file = source / "SKILL.md"
        inferred_name = source.name
    else:
        skill_file = source
        inferred_name = skill_file.parent.name
    if not skill_file.is_file():
        raise RuntimeError(f"No SKILL.md found in {source}")

    skill_name = (name or inferred_name).strip()
    if not skill_name:
        raise RuntimeError("Skill name cannot be empty.")

    target_dir = _managed_skill_root(scope, cwd) / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill_file, target_dir / "SKILL.md")
    return {"name": skill_name, "targetPath": str(target_dir / "SKILL.md")}


def remove_managed_skill(cwd: str | Path, name: str, scope: str = "user") -> dict[str, object]:
    """删除托管目录里的一个 skill；不存在则返回 removed=False。"""
    target_path = _managed_skill_root(scope, cwd) / name
    if not target_path.exists():
        return {"removed": False, "targetPath": str(target_path)}
    shutil.rmtree(target_path)
    return {"removed": True, "targetPath": str(target_path)}
