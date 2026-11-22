"""Water 连接器：叮咚买菜（Dingdong）只读上下文提取骨架。

定位：仅用于提取饮食偏好、忌口与基础常购食材习惯，
绝不抓取具体账单金额与精确门牌地址。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONNECTOR_ID = "dingdong"


def _has_dingdong_skill() -> Path | None:
    candidates = [
        Path.home() / ".gemini/config/skills/dingdong-ai/SKILL.md",
        Path.home() / ".agents/skills/dingdong-ai/SKILL.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def discover() -> dict[str, Any]:
    skill = _has_dingdong_skill()
    if skill:
        return {
            "name": "叮咚买菜 (饮食与常购偏好)",
            "available": True,
            "detail": f"检测到本地 Skill: {skill}（当前为骨架，建议通过 MCP 实时查询并顺手蒸馏偏好）",
        }
    return {
        "name": "叮咚买菜 (饮食与常购偏好)",
        "available": False,
        "detail": "未检测到 dingdong-ai Skill",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    # 预留接口：高频买菜场景优先由宿主 Agent 实时调 MCP 完成，
    # 此处仅在需要做一次性历史常购品味分析时接入只读摘要。
    return "", 0, []
