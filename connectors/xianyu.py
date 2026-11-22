"""Water 连接器：闲鱼（Xianyu）只读上下文提取骨架。

定位：仅用于提取个人持有设备资产线索与二手交易避坑偏好，
绝不抓取首页推荐流、询价私信与具体消费金额。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONNECTOR_ID = "xianyu"


def _has_xianyu_skill() -> Path | None:
    candidates = [
        Path.home() / ".gemini/config/skills/xianyu-ai/SKILL.md",
        Path.home() / ".agents/skills/xianyu-ai/SKILL.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def discover() -> dict[str, Any]:
    skill = _has_xianyu_skill()
    if skill:
        return {
            "name": "闲鱼 (设备资产与交易偏好)",
            "available": True,
            "detail": f"检测到本地 Skill: {skill}（当前为骨架，需按实际本地 CLI/MCP 接入只读拉取）",
        }
    return {
        "name": "闲鱼 (设备资产与交易偏好)",
        "available": False,
        "detail": "未检测到 xianyu-ai Skill",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    # 预留接口：接入本机 xianyu 只读命令或导出数据后，
    # 仅输出「核心硬件资产变更」与「验机/交易偏好」摘要。
    return "", 0, []
