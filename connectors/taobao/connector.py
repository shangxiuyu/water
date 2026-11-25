"""Tree Seed 按需连接器：淘宝 / 淘宝闪购 / 盒马鲜生（自包含实现，依赖 playwright）。"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "taobao"
CONNECTOR_NAME = "淘宝 / 闪购 / 盒马 (消费与商圈偏好)"
PKG_DIR = Path(__file__).resolve().parent


def _sync_legacy_auth() -> None:
    for cand in (
        Path.home() / ".agents/skills/taobao-ai",
        Path.home() / ".gemini/config/skills/ali-lifestyle",
        Path.home() / "Desktop/taobao",
    ):
        if not cand.is_dir():
            continue
        env_dst = PKG_DIR / ".env"
        if not env_dst.is_file() and (cand / ".env").is_file():
            shutil.copy2(cand / ".env", env_dst)
        data_dst = PKG_DIR / "data"
        if not data_dst.is_dir() and (cand / "data").is_dir():
            shutil.copytree(cand / "data", data_dst, dirs_exist_ok=True)


def _load_clients():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    _sync_legacy_auth()
    from ali_client import AliLifestyleClient  # type: ignore
    from hema_client import HemaClient  # type: ignore

    return AliLifestyleClient(), HemaClient


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
        client, _ = _load_clients()
        res = client.get_user_info()
        logged = bool(res.get("success"))
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": logged,
            "needs_login": not logged,
            "memory_files": [],
            "chat_count": 1 if logged else 0,
            "unit_label": "已登录" if logged else "可扫码",
            "detail": "" if logged else "未登录，请运行 `tree seed login taobao`",
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
        client, HemaClient = _load_clients()
    except ImportError as e:
        print(f"❌ 缺少依赖 ({e})，请先运行: pip install playwright && playwright install chromium", file=sys.stderr)
        return False
    cookie = HemaClient.interactive_login()
    return bool(cookie)


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client, _ = _load_clients()
    limit = max(3, min(max_sessions, 15))
    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# 淘宝 / 闪购 / 盒马偏好线索（已剥离门牌号、手机号、订单号与消费金额，严禁流水入树）",
        f"> 提取时间: {now_iso()} | 仅用于提炼常驻城市商圈、常购食材与硬件耗材偏好",
        "",
    ]
    pids: list[str] = []

    addr_res = client.get_addresses()
    if addr_res.get("success") and addr_res.get("addresses"):
        lines.append("## 常用商圈与履约站点（已隐去具体门牌号与联系人）")
        for a in (addr_res.get("addresses") or [])[:5]:
            poi = str(a.get("poi_name") or "").strip()
            store = (a.get("assigned_store") or {}).get("group_name") or ""
            if poi or store:
                entry = f"- 区域/POI: {redact(poi)}" + (f"（对应盒马站: {redact(store)}）" if store else "")
                lines.append(entry)
                pids.append(short_hash(f"taobao:poi:{poi}:{store}"))
        lines.append("")

    ord_res = client.get_orders(source="all", limit=limit)
    for key, title in (
        ("hema_orders", "盒马生鲜购买线索"),
        ("shangou_orders", "淘宝闪购/外卖线索"),
        ("taobao_orders", "淘宝电商购买线索"),
    ):
        olist = ord_res.get(key) or []
        if not olist:
            continue
        lines.append(f"## {title}")
        for o in olist[:limit]:
            shop = str(o.get("shop_name") or o.get("seller") or "").strip()
            item_titles = [str(it.get("title") or "").strip() for it in (o.get("items") or []) if it.get("title")]
            if not shop and not item_titles:
                continue
            summary = f"【{redact(shop)}】" if shop else ""
            if item_titles:
                summary += (" " if summary else "") + "、".join(redact(t) for t in item_titles[:5])
            lines.append(f"- {summary}")
            pids.append(short_hash(f"taobao:{key}:{summary[:40]}"))
        lines.append("")

    if not pids:
        return "", 0, []
    return "\n".join(lines).strip() + "\n", len(pids), pids
