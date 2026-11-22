"""Water 连接器：淘宝 / 淘宝闪购 / 盒马鲜生（Taobao & Ali Lifestyle）闭环实现。"""

from __future__ import annotations

import time
from typing import Any

from _bridge import find_toolkit_cli, redact_lifestyle_text, run_cli, standard_discover

CONNECTOR_ID = "taobao"
SKILL_NAMES = ["hema-ai", "ali-lifestyle"]


def discover() -> dict[str, Any]:
    return standard_discover("淘宝 / 闪购 / 盒马 (消费与地址偏好)", SKILL_NAMES, ["user"], env_var="WATER_TAOBAO_CLI")


def login_qr(timeout: int = 180) -> bool:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_TAOBAO_CLI")
    if not cli:
        return False
    code, _ = run_cli(cli, ["login"], timeout=timeout, interactive=True)
    return code == 0


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    cli = find_toolkit_cli(SKILL_NAMES, env_var="WATER_TAOBAO_CLI")
    if not cli:
        return "", 0, []

    limit = max(3, min(max_sessions, 20))
    sections: list[str] = []
    c1, o1 = run_cli(cli, ["addresses"], timeout=25)
    if c1 == 0 and o1.strip():
        sections.append(f"## 常用区域线索（写入 Tree 时仅保留城市/商圈，剥离门牌号）\n\n{o1.strip()}")

    c2, o2 = run_cli(cli, ["orders", "--source", "all", "--limit", str(limit)], timeout=30)
    if c2 == 0 and o2.strip():
        sections.append(f"## 近期消费线索\n\n{o2.strip()}")

    if not sections:
        return "", 0, []

    redact = helpers.get("redact_secrets")
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    body = redact_lifestyle_text("\n\n".join(sections), redact)
    md = "\n".join([
        "# 淘宝 / 盒马消费线索（仅用于提炼常用设备耗材型号、饮食与选品偏好，严禁流水入树）",
        f"> 提取时间: {now_iso()}",
        "",
        body,
    ])
    return md, len(sections), [short_hash(f"taobao:{len(body)}")]
