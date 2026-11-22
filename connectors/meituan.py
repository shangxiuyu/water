"""Water 连接器：美团（Meituan）只读上下文提取骨架。

定位：仅用于提取饮食忌口、口味倾向与常驻商圈偏好，
绝不抓取单笔外卖/团购账单、单车骑行轨迹与精确门牌地址。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONNECTOR_ID = "meituan"


def _has_meituan_skill() -> Path | None:
    candidates = [
        Path.home() / ".gemini/config/skills/meituan-ai/SKILL.md",
        Path.home() / ".agents/skills/meituan-ai/SKILL.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def discover() -> dict[str, Any]:
    skill = _has_meituan_skill()
    if skill:
        return {
            "name": "美团 (饮食与本地生活偏好)",
            "available": True,
            "detail": f"检测到本地 Skill: {skill}（建议日常由 MCP 实时下单，仅按需蒸馏生活偏好）",
        }
    return {
        "name": "美团 (饮食与本地生活偏好)",
        "available": False,
        "detail": "未检测到 meituan-ai Skill",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    # 预留接口：接入本机只读生活偏好摘要后，
    # 仅输出「饮食忌口/口味」「常驻商圈」「到店娱乐偏好」。
    return "", 0, []
