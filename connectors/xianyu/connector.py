"""Tree Seed 按需连接器：闲鱼（自包含实现，依赖 ego-browser 或本地 Cookie）。"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "xianyu"
CONNECTOR_NAME = "闲鱼 (硬件资产与闲置流转)"
PKG_DIR = Path(__file__).resolve().parent


def _sync_legacy_auth() -> None:
    for cand in (
        Path.home() / ".gemini/antigravity/scratch/xianyu-ai",
        Path.home() / ".agents/skills/xianyu-ai",
    ):
        if not cand.is_dir():
            continue
        env_dst = PKG_DIR / ".env"
        if not env_dst.is_file() and (cand / ".env").is_file():
            shutil.copy2(cand / ".env", env_dst)
        data_dst = PKG_DIR / "data"
        if not data_dst.is_dir() and (cand / "data").is_dir():
            shutil.copytree(cand / "data", data_dst, dirs_exist_ok=True)


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    _sync_legacy_auth()
    from xianyu_client import XianyuClient  # type: ignore

    return XianyuClient()


def discover() -> dict[str, Any]:
    try:
        client = _load_client()
        st = client.check_login_status()
        logged = bool(st.get("logged_in"))
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": logged,
            "needs_login": not logged,
            "memory_files": [],
            "chat_count": 1 if logged else 0,
            "unit_label": "已登录" if logged else "可扫码",
            "detail": "" if logged else "未登录，请运行 `tree seed login xianyu`",
        }
    except Exception as e:
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": False,
            "needs_login": True,
            "memory_files": [],
            "chat_count": 0,
            "detail": f"待初始化: {e}",
        }


def login_qr(timeout: int = 180) -> bool:
    client = _load_client()
    res = client.interactive_login(timeout_seconds=timeout)
    return bool(res.get("success"))


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client = _load_client()
    limit = max(3, min(max_sessions, 20))
    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# 闲鱼硬件资产与闲置流转线索（已剥离交易金额与私聊记录，严禁流水入树）",
        f"> 提取时间: {now_iso()} | 仅用于提炼持有/出让的核心硬件资产与折腾方向",
        "",
    ]
    pids: list[str] = []

    bought = client.get_orders(limit=limit)
    if bought.get("success") and bought.get("orders"):
        lines.append("## 已购入的闲置物品标题")
        for o in (bought.get("orders") or [])[:limit]:
            title = str(o.get("title") or "").strip()
            status = str(o.get("status") or "").strip()
            if title:
                lines.append(f"- [{status or '交易'}] {redact(title)}")
                pids.append(short_hash(f"xianyu:buy:{title[:40]}"))
        lines.append("")

    pub = client.get_my_published(limit=limit)
    if pub.get("success") and pub.get("published_items"):
        lines.append("## 我发布的闲置物品标题")
        for it in (pub.get("published_items") or [])[:limit]:
            title = str(it.get("title") or "").strip()
            if title:
                lines.append(f"- {redact(title)}")
                pids.append(short_hash(f"xianyu:pub:{title[:40]}"))
        lines.append("")

    if not pids:
        return "", 0, []
    return "\n".join(lines).strip() + "\n", len(pids), pids
