"""Tree Seed 按需连接器：京东（自包含实现，依赖 requests 与 playwright）。"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "jd"
CONNECTOR_NAME = "京东 (硬件设备与耗材线索)"
PKG_DIR = Path(__file__).resolve().parent


def _sync_legacy_auth() -> None:
    legacy = Path.home() / ".agents/skills/jd-ai"
    if not legacy.is_dir():
        return
    data_dst = PKG_DIR / "data"
    if not data_dst.is_dir() and (legacy / "data").is_dir():
        shutil.copytree(legacy / "data", data_dst, dirs_exist_ok=True)


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    _sync_legacy_auth()
    from jd_client import JDClient  # type: ignore

    return JDClient()


def discover() -> dict[str, Any]:
    missing = []
    for mod_name in ("requests", "playwright"):
        try:
            __import__(mod_name)
        except ImportError:
            missing.append(mod_name)
    if missing:
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": False,
            "needs_login": True,
            "memory_files": [],
            "chat_count": 0,
            "detail": f"缺少依赖 {', '.join(missing)}，请运行: pip install requests playwright && playwright install chromium",
        }
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
            "detail": "" if logged else "未登录，请运行 `tree seed login jd`",
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
        print(f"❌ 缺少依赖 ({e})，请先运行: pip install requests playwright && playwright install chromium", file=sys.stderr)
        return False
    res = client.interactive_login(mode="qr")
    return bool(res.get("success"))


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client = _load_client()
    res = client.get_orders(page_num=1)
    if not res.get("success"):
        return "", 0, []

    orders = (res.get("orders") or [])[: max(5, min(max_sessions, 25))]
    if not orders:
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# 京东硬件资产与常购耗材线索（已剥离订单号、金额、收货人与物流轨迹，严禁流水入树）",
        f"> 提取时间: {now_iso()} | 仅用于提炼长期持有的硬件型号与常用耗材偏好",
        "",
    ]
    pids: list[str] = []
    for o in orders:
        deal_date = str(o.get("deal_time") or "")[:10]
        shop = str(o.get("shop") or "").strip()
        titles = [str(p.get("title") or "").strip() for p in (o.get("products") or []) if p.get("title")]
        if not titles:
            continue
        shop_tag = f"【{redact(shop)}】" if shop else ""
        joined = "；".join(redact(t) for t in titles[:4])
        lines.append(f"- [{deal_date or '近期'}] {shop_tag}{joined}")
        pids.append(short_hash(f"jd:{deal_date}:{joined[:40]}"))

    if not pids:
        return "", 0, []
    return "\n".join(lines) + "\n", len(pids), pids
