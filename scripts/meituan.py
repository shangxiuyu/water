"""Water 连接器：美团（Meituan）闭环实现。"""

from __future__ import annotations

import time
from typing import Any

from _bridge import find_toolkit_cli, redact_lifestyle_text, run_cli, standard_discover

CONNECTOR_ID = "meituan"
SKILL_NAMES = ["meituan-ai"]


def discover() -> dict[str, Any]:
    return standard_discover("美团 (饮食与本地生活偏好)", SKILL_NAMES, ["user"], env_var="WATER_MEITUAN_CLI")


def login_qr(timeout: int = 180) -> bool:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_MEITUAN_CLI")
    if not cli:
        return False
    code, _ = run_cli(cli, ["login"], timeout=timeout, interactive=True)
    return code == 0


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_MEITUAN_CLI")
    if not cli:
        return "", 0, []

    limit = max(3, min(max_sessions, 20))
    code, out = run_cli(cli, ["orders", "--limit", str(limit)], timeout=30)
    if code != 0 or not out.strip():
        return "", 0, []

    redact = helpers.get("redact_secrets")
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    cleaned = redact_lifestyle_text(out, redact)
    md = "\n".join([
        "# 美团本地生活线索（仅用于提炼饮食口味、常驻商圈与到店偏好，严禁流水入树）",
        f"> 提取时间: {now_iso()}",
        "",
        cleaned,
    ])
    return md, 1, [short_hash(f"meituan:{len(cleaned)}")]
