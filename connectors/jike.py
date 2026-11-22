"""Water 连接器：即刻（Jike）只读上下文提取。

只读取本机已配置的 `jike-ai` 工具输出的个人收藏/动态摘要，
过滤噪音并脱敏后交给 Tree `seed/staging` 进行认知蒸馏。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

CONNECTOR_ID = "jike"


def _find_jike_cli() -> Path | None:
    candidates = [
        Path.home() / ".agents/skills/jike-ai/cli.py",
        Path.home() / ".gemini/config/skills/jike-ai/cli.py",
        Path.home() / "Desktop/jike/cli.py",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def discover() -> dict[str, Any]:
    cli = _find_jike_cli()
    if cli:
        return {
            "name": "即刻 (收藏与原创观点)",
            "available": True,
            "detail": f"检测到本地工具: {cli}",
        }
    return {
        "name": "即刻 (收藏与原创观点)",
        "available": False,
        "detail": "未检测到 jike-ai cli.py，可先安装 jike-ai Skill",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    cli = _find_jike_cli()
    if not cli or not shutil.which("python3"):
        return "", 0, []

    redact = helpers.get("redact_secrets", lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: "")

    limit = max(5, min(max_sessions, 30))
    try:
        proc = subprocess.run(
            ["python3", str(cli), "collections", "--max", str(limit), "--llm"],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except Exception:
        return "", 0, []

    raw = (proc.stdout or "").strip()
    if proc.returncode != 0 or not raw:
        return "", 0, []

    lines = [
        "# 即刻个人收藏精华（待蒸馏进 01-关于我 / 02-我的思考）",
        f"> 提取时间: {now_iso()} | 仅保留收藏内容用于提炼关注领域与独立判断",
        "",
        redact(raw),
    ]
    md = "\n".join(lines)
    pid = short_hash(f"jike:collections:{len(raw)}")
    return md, 1, [pid]
