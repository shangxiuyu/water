"""Water 连接器：飞猪（Fliggy）只读差旅偏好提取骨架。

定位：仅用于提取航班座位/直飞偏好、酒店房型要求与高频出差城市，
严禁读取身份证号、护照号、具体行程单与订单金额。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONNECTOR_ID = "fliggy"


def _has_fliggy_skill() -> Path | None:
    candidates = [
        Path.home() / ".gemini/config/skills/fliggy-ai/SKILL.md",
        Path.home() / ".agents/skills/fliggy-ai/SKILL.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def discover() -> dict[str, Any]:
    skill = _has_fliggy_skill()
    if skill:
        return {
            "name": "飞猪 (差旅与航旅偏好)",
            "available": True,
            "detail": f"检测到本地 Skill: {skill}（仅提取出行硬偏好，严格屏蔽证件与行程隐私）",
        }
    return {
        "name": "飞猪 (差旅与航旅偏好)",
        "available": False,
        "detail": "未检测到 fliggy-ai Skill（可按需挂载或由导出摘要提炼）",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    # 预留接口：仅输出「航司/座位/直飞偏好」「酒店无烟/楼层偏好」「高频出差城市」。
    return "", 0, []
