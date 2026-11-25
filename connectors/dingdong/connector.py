"""Tree Seed 按需连接器：叮咚买菜（自包含实现，依赖 playwright）。"""

from __future__ import annotations

import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

CONNECTOR_ID = "dingdong"
CONNECTOR_NAME = "叮咚买菜 (常购食材与饮食偏好)"
PKG_DIR = Path(__file__).resolve().parent


def _sync_legacy_auth() -> None:
    for cand in (
        Path.home() / ".agents/skills/dingdong-ai",
        Path.home() / "Desktop/dingdong",
    ):
        if not cand.is_dir():
            continue
        env_dst = PKG_DIR / ".env"
        if not env_dst.is_file() and (cand / ".env").is_file():
            shutil.copy2(cand / ".env", env_dst)


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    _sync_legacy_auth()
    from dingdong_client import DingDongClient  # type: ignore

    return DingDongClient(), DingDongClient


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
        client, _ = _load_client()
        res = client.get_user_info()
        logged = bool(res.get("success"))
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": logged,
            "needs_login": not logged,
            "memory_files": [],
            "chat_count": 1 if logged else 0,
            "unit_label": "已登录" if logged else "可登录",
            "detail": "" if logged else "未登录，请运行 `tree seed login dingdong`",
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
        _, DingDongClient = _load_client()
    except ImportError as e:
        print(f"❌ 缺少依赖 ({e})，请先运行: pip install playwright && playwright install chromium", file=sys.stderr)
        return False
    cookie = DingDongClient.interactive_login()
    return bool(cookie)


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client, _ = _load_client()
    limit = max(5, min(max_sessions, 20))
    res = client.get_order_history(page=1, limit=limit)
    if not res.get("success"):
        return "", 0, []

    orders = res.get("orders") or []
    if not orders:
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    prod_counts: Counter[str] = Counter()
    order_lines: list[str] = []
    pids: list[str] = []

    for o in orders:
        date_s = str(o.get("create_time_str") or "")[:10]
        pnames = [str(p.get("name") or "").strip() for p in (o.get("products") or []) if p.get("name")]
        for n in pnames:
            prod_counts[n] += 1
        if pnames:
            joined = "、".join(redact(x) for x in pnames[:8])
            order_lines.append(f"- [{date_s or '近期'}] {joined}")
            pids.append(short_hash(f"dingdong:{date_s}:{joined[:30]}"))

    if not pids:
        return "", 0, []

    lines = [
        "# 叮咚买菜常购生鲜线索（已剥离订单号、菜价与收货地址，严禁流水入树）",
        f"> 提取时间: {now_iso()} | 样本数: {len(orders)} 单",
        "",
    ]
    repeats = [(n, c) for n, c in prod_counts.most_common(15) if c >= 2]
    if repeats:
        lines.append("## 多次复购的高频食材")
        lines.append("、".join(f"{redact(n)}({c}次)" for n, c in repeats))
        lines.append("")

    lines.append("## 近期采买组合样本")
    lines.extend(order_lines)
    return "\n".join(lines).strip() + "\n", len(pids), pids
