from __future__ import annotations

from pathlib import Path

from .schemas import SCHEMAS
from .tools import HANDLERS


def register(ctx) -> None:
    for schema in SCHEMAS:
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset="haven-market-model",
            schema=schema,
            handler=HANDLERS[name],
            description=schema["description"],
        )

    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md)

    def handle_haven(raw_args: str) -> str:
        command = raw_args.strip().lower()
        if command in {"", "status", "状态"}:
            return HANDLERS["haven_model_status"](
                {"refresh": False, "include_shadow": True}
            )
        if command in {"refresh", "update", "更新"}:
            return HANDLERS["haven_refresh_close"]({})
        return (
            "Usage: /haven [status|refresh]. "
            "For TQQQ calls, ask Hermes with your share count and cost basis."
        )

    ctx.register_command(
        "haven",
        handler=handle_haven,
        description="Show or refresh the Haven v0.4 market model",
    )
