"""技能加载器（简化版）。

规则：
- 在 `.codex/skills/**/SKILL.md` 查找技能定义。
- 若用户输入包含技能名或 `$skill_name`，则视为命中。
"""

from __future__ import annotations

from pathlib import Path


class SkillLoader:
    """按文本匹配触发技能内容加载。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def relevant_skills(self, user_text: str) -> list[tuple[str, str]]:
        """返回命中的技能列表（名称 + 内容片段）。"""
        skills_dir = self.workspace / ".codex" / "skills"
        if not skills_dir.exists():
            return []

        matched: list[tuple[str, str]] = []
        query = user_text.lower()

        for skill_md in skills_dir.glob("**/SKILL.md"):
            name = skill_md.parent.name
            hit = name.lower() in query or f"${name.lower()}" in query
            if not hit:
                continue

            content = skill_md.read_text(encoding="utf-8")
            # 只截取前 4000 字符，避免一次注入过长。
            matched.append((name, content[:4000]))

        return matched
