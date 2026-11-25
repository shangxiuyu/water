"""Tree Seed 按需连接器：美团（自包含实现，依赖 playwright）。"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "meituan"
CONNECTOR_NAME = "美团 (饮食与商圈偏好)"
PKG_DIR = Path(__file__).resolve().parent


def _sync_legacy_auth() -> None:
    """若本机旧版 ~/.agents/skills/meituan-ai 已有登录凭证且当前目录未初始化，只读复用副本。"""
    legacy = Path.home() / ".agents/skills/meituan-ai"
    if not legacy.is_dir():
        return
    env_dst = PKG_DIR / ".env"
    if not env_dst.is_file() and (legacy / ".env").is_file():
        shutil.copy2(legacy / ".env", env_dst)
    data_dst = PKG_DIR / "data"
    if not data_dst.is_dir() and (legacy / "data").is_dir():
        shutil.copytree(legacy / "data", data_dst, dirs_exist_ok=True)


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    _sync_legacy_auth()
    from meituan_client import MeituanClient  # type: ignore

    return MeituanClient()


def discover() -> dict[str, Any]:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": False,
            "needs_login": True,
            "memory_files": [],
            "chat_count": 0,
            "detail": "缺少依赖 playwright，请运行: pip install playwright && playwright install chromium",
        }
    try:
        client = _load_client()
        info = client.get_user_info()
        logged = bool(info.get("logged_in"))
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": logged,
            "needs_login": not logged,
            "memory_files": [],
            "chat_count": 1 if logged else 0,
            "unit_label": "已登录" if logged else "可扫码",
            "detail": "" if logged else "未登录，请运行 `tree seed login meituan`",
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
    try:
        client = _load_client()
    except ImportError as e:
        print(f"❌ 缺少依赖 ({e})，请先运行: pip install playwright && playwright install chromium", file=sys.stderr)
        return False
    client.interactive_login()
    info = client.get_user_info()
    return bool(info.get("logged_in"))


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client = _load_client()
    limit = max(5, min(max_sessions, 25))
    res = client.get_orders(limit=limit, page=1, category="all")
    if not res.get("success"):
        return "", 0, []

    orders = res.get("orders") or []
    if not orders:
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# 美团本地生活偏好线索（已剥离订单号与消费金额，仅用于提炼饮食口味、常点品类与商圈偏好）",
        f"> 提取时间: {now_iso()} | 样本数: {len(orders)} 笔（严禁流水入树，写入 01-关于我 时标 --no-always）",
        "",
    ]
    pids: list[str] = []
    for o in orders:
        cat_name = str(o.get("category_name") or "本地生活").strip()
        shop = str(o.get("shop") or "").strip()
        t_str = str(o.get("time") or "")[:10]
        if not shop:
            continue
        lines.append(f"- [{t_str or '近期'}]（{cat_name}）{redact(shop)}")
        pids.append(short_hash(f"meituan:{t_str}:{shop}"))

    if not pids:
        return "", 0, []
    return "\n".join(lines) + "\n", len(pids), pids
