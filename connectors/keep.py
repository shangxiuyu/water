"""Water 连接器：Keep（运动健康）只读上下文提取骨架。

定位：仅在用户明确要求记住运动习惯或训练目标时提取慢变量，
严禁默认提取心率、体脂、医疗健康明细与单次 GPS 轨迹流水。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONNECTOR_ID = "keep"


def _has_keep_skill() -> Path | None:
    candidates = [
        Path.home() / ".gemini/config/skills/keep-ai/SKILL.md",
        Path.home() / ".agents/skills/keep-ai/SKILL.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def discover() -> dict[str, Any]:
    skill = _has_keep_skill()
    if skill:
        return {
            "name": "Keep (运动习惯与目标)",
            "available": True,
            "detail": f"检测到本地 Skill: {skill}（受健康隐私红线保护，需用户明确授权后再蒸馏）",
        }
    return {
        "name": "Keep (运动习惯与目标)",
        "available": False,
        "detail": "未检测到 keep-ai Skill",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    # 预留接口：遵守 Tree 健康隐私红线，不自动提取生理指标与单次运动流水，
    # 仅在用户显式确认后输出「长期运动项目」「阶段训练目标」「运动装备」。
    return "", 0, []
