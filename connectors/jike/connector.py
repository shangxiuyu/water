"""Tree Seed 按需连接器：即刻 Jike（纯 Python 标准库零依赖，扫码即连）。"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "jike"
CONNECTOR_NAME = "即刻 Jike (精选收藏)"
PKG_DIR = Path(__file__).resolve().parent


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    import jike_client  # type: ignore

    return jike_client


def _mtime_iso(p: Path) -> str:
    try:
        return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return ""


def discover() -> dict[str, Any]:
    client = _load_client()
    tokens = client.load_tokens()
    src_p = Path(tokens["source_path"]) if tokens.get("source_path") else None
    latest = _mtime_iso(src_p) if (src_p and src_p.is_file()) else ""
    has_token = bool(tokens.get("access_token"))
    return {
        "id": CONNECTOR_ID,
        "name": CONNECTOR_NAME,
        "available": has_token,
        "needs_login": not has_token,
        "memory_files": [],
        "chat_count": 1 if has_token else 0,
        "unit_label": "已登录" if has_token else "可扫码",
        "latest_date": latest,
        "detail": "" if has_token else "未登录，请运行 `tree seed login jike`",
    }


def login_qr(timeout: int = 180) -> bool:
    client = _load_client()
    return bool(client.login_qr(timeout=timeout))


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client = _load_client()
    tokens = client.load_tokens()
    if not tokens.get("access_token"):
        return "", 0, []

    target = max(5, min(max_sessions, 100))
    items = client.fetch_collections(tokens, limit=target)
    if not items:
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# 即刻个人收藏精华（待蒸馏进 01-关于我 / 02-我的思考，流水不入树）",
        f"> 提取时间: {now_iso()} | 共 {len(items)} 条收藏（仅保留正文、圈子、作者与出处）",
        "",
    ]
    pids: list[str] = []
    for raw in items:
        pid = str(raw.get("id") or "")
        user = (raw.get("user") or {}).get("screenName") or "匿名即友"
        topic = (raw.get("topic") or {}).get("content") or ""
        content = (raw.get("content") or "").strip()
        link = (raw.get("linkInfo") or {}).get("linkUrl") or ""
        created = str(raw.get("createdAt") or "")[:10]
        if not content and not link:
            continue
        header = f"### [{created or '未知日期'}] {user}" + (f" · #{topic}" if topic else "")
        lines.append(header)
        if content:
            lines.append(redact(content))
        if link:
            lines.append(f"- 外链: {link}")
        if pid:
            lines.append(f"- 原帖: https://web.okjike.com/originalPost/{pid}")
            pids.append(short_hash(f"jike:{pid}"))
        lines.append("")

    if not pids:
        return "", 0, []
    return "\n".join(lines).strip() + "\n", len(pids), pids
