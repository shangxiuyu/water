"""Water 连接器：闲鱼（Xianyu）闭环实现。"""

from __future__ import annotations

import time
from typing import Any

from _bridge import find_toolkit_cli, redact_lifestyle_text, run_cli, standard_discover

CONNECTOR_ID = "xianyu"
SKILL_NAMES = ["xianyu-ai"]


def discover() -> dict[str, Any]:
    return standard_discover("闲鱼 (设备资产与交易偏好)", SKILL_NAMES, ["status"], env_var="WATER_XIANYU_CLI")


def login_qr(timeout: int = 180) -> bool:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_XIANYU_CLI")
    if not cli:
        return False
    code, _ = run_cli(cli, ["login"], timeout=timeout, interactive=True)
    return code == 0


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_XIANYU_CLI")
    if not cli:
        return "", 0, []

    limit = max(3, min(max_sessions, 20))
    sections: list[str] = []
    for sub, title in [("orders", "已买到的宝贝线索"), ("published", "我发布的闲置线索")]:
        code, out = run_cli(cli, [sub, "--limit", str(limit)], timeout=30)
        if code == 0 and out.strip():
            sections.append(f"## {title}\n\n{out.strip()}")

    if not sections:
        return "", 0, []

    redact = helpers.get("redact_secrets")
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    body = redact_lifestyle_text("\n\n".join(sections), redact)
    md = "\n".join([
        "# 闲鱼资产与交易线索（仅用于提炼设备资产清单与交易偏好，严禁流水入树）",
        f"> 提取时间: {now_iso()}",
        "",
        body,
    ])
    return md, len(sections), [short_hash(f"xianyu:{len(body)}")]
