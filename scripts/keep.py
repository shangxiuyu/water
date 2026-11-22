"""Water 连接器：Keep（运动健康）闭环实现。

遵守 Tree 健康隐私红线：仅提取长期运动项目偏好与训练目标线索，
不采集单次心率曲线与 GPS 轨迹，且写入 Tree 前需用户明确确认。
"""

from __future__ import annotations

import time
from typing import Any

from _bridge import find_toolkit_cli, redact_lifestyle_text, run_cli, standard_discover

CONNECTOR_ID = "keep"
SKILL_NAMES = ["keep-ai"]


def discover() -> dict[str, Any]:
    return standard_discover("Keep (运动习惯与目标)", SKILL_NAMES, ["status"], env_var="WATER_KEEP_CLI")


def login_qr(timeout: int = 180) -> bool:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_KEEP_CLI")
    if not cli:
        return False
    code, _ = run_cli(cli, ["login"], timeout=timeout, interactive=True)
    return code == 0


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_KEEP_CLI")
    if not cli:
        return "", 0, []

    limit = max(3, min(max_sessions, 15))
    sections: list[str] = []
    c1, o1 = run_cli(cli, ["summary"], timeout=25)
    if c1 == 0 and o1.strip():
        sections.append(f"## 累计运动概览\n\n{o1.strip()}")

    c2, o2 = run_cli(cli, ["workouts", "--limit", str(limit)], timeout=25)
    if c2 == 0 and o2.strip():
        sections.append(f"## 近期运动项目分布\n\n{o2.strip()}")

    if not sections:
        return "", 0, []

    redact = helpers.get("redact_secrets")
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    body = redact_lifestyle_text("\n\n".join(sections), redact)
    md = "\n".join([
        "# Keep 运动习惯线索（受健康隐私红线保护，需用户明确同意后才写入 Tree）",
        f"> 提取时间: {now_iso()}",
        "",
        body,
    ])
    return md, len(sections), [short_hash(f"keep:{len(body)}")]
