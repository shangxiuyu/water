"""Tree Seed 按需连接器：Keep（免 Playwright，直接弹出官方高清二维码图片扫码；遵守健康隐私红线）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

CONNECTOR_ID = "keep"
CONNECTOR_NAME = "Keep (运动习惯与阶段目标)"
PKG_DIR = Path(__file__).resolve().parent


def _sync_legacy_auth() -> None:
    legacy = Path.home() / ".agents/skills/keep-ai"
    if not legacy.is_dir():
        return
    data_dst = PKG_DIR / "data"
    if not data_dst.is_dir() and (legacy / "data").is_dir():
        shutil.copytree(legacy / "data", data_dst, dirs_exist_ok=True)
    for fname in (".env", "session.json", "keep_session.json"):
        if not (PKG_DIR / fname).is_file() and (legacy / fname).is_file():
            shutil.copy2(legacy / fname, PKG_DIR / fname)


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    _sync_legacy_auth()
    from keep_client import KeepClient  # type: ignore

    return KeepClient()


def _open_qr_image_window(img_path: Path) -> None:
    """调用系统原生看图窗口弹出高清二维码图片（严禁在终端打印变形字符码）。"""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", "Preview", str(img_path)], stderr=subprocess.DEVNULL, check=False)
        elif sys.platform.startswith("win"):
            os.startfile(str(img_path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(img_path)], stderr=subprocess.DEVNULL, check=False)
    except Exception:
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(img_path)], stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass


def discover() -> dict[str, Any]:
    try:
        import requests  # noqa: F401
    except ImportError:
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": False,
            "needs_login": True,
            "memory_files": [],
            "chat_count": 0,
            "detail": "缺少依赖 requests，请运行: pip install requests",
        }
    try:
        client = _load_client()
        logged = bool(client.is_authenticated)
        return {
            "id": CONNECTOR_ID,
            "name": CONNECTOR_NAME,
            "available": logged,
            "needs_login": not logged,
            "memory_files": [],
            "chat_count": 1 if logged else 0,
            "unit_label": "已登录" if logged else "可扫码",
            "detail": "" if logged else "未登录，请运行 `tree seed login keep`",
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
        print(f"❌ 缺少依赖 ({e})，请先运行: pip install requests", file=sys.stderr)
        return False

    qr_info = client.create_qrcode()
    qid = qr_info.get("qrcode_id")
    img_url = qr_info.get("image_url")
    if not qid or not img_url:
        print("❌ 获取 Keep 官方登录二维码失败。", file=sys.stderr)
        return False

    qr_img_path = Path(tempfile.gettempdir()) / "tree_keep_qr.png"
    req = urllib.request.Request(str(img_url), headers={"User-Agent": "Keep/8.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        qr_img_path.write_bytes(resp.read())

    _open_qr_image_window(qr_img_path)
    print("\n📱 已在屏幕弹出高清登录二维码图片，请打开手机【Keep App】右上角【扫一扫】确认登录：")
    print(f"   二维码图片路径: {qr_img_path}")
    print(f"   等待手机确认中（超时 {timeout}s）...")
    sys.stdout.flush()

    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            time.sleep(1.5)
            res = client.check_qrcode(qid)
            if res.get("ok"):
                data = res.get("data") or {}
                token = data.get("token") or ((data.get("user") or {}).get("token") if isinstance(data, dict) else None)
                if not token and isinstance(data, str):
                    token = data
                if token:
                    session = client.set_token(token)
                    print(f"✅ Keep 扫码登录成功！欢迎回来，{session.get('username') or 'Keep 用户'}。")
                    return True
    finally:
        qr_img_path.unlink(missing_ok=True)

    print("❌ 登录超时，请重试。", file=sys.stderr)
    return False


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client = _load_client()
    if not client.is_authenticated:
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# Keep 运动习惯线索（不采单次心率曲线与 GPS 轨迹；写入 Tree 前须经用户亲口确认）",
        f"> 提取时间: {now_iso()}",
        "",
    ]
    pids: list[str] = []

    try:
        user = client.get_user_info()
        summary = client.get_sports_summary()
        if getattr(user, "preferences", None):
            lines.append(f"- 账号设置运动偏好: {'、'.join(user.preferences)}")
        summary_md = client.format_summary_markdown(user, summary)
        if summary_md:
            lines.append("\n## 累计运动概览")
            lines.append(redact(summary_md))
            pids.append(short_hash(f"keep:summary:{len(summary_md)}"))
    except Exception:
        pass

    try:
        wres = client.list_workouts(sport_type="all", limit=max(5, min(max_sessions, 20)))
        items = wres.get("items") or []
        if items:
            type_counts = Counter(str(it.get("sport_type") or "运动") for it in items)
            lines.append("\n## 近期运动项目分布")
            lines.append("、".join(f"{k}({v}次)" for k, v in type_counts.most_common()))
            pids.append(short_hash(f"keep:workouts:{len(items)}"))
    except Exception:
        pass

    if not pids:
        return "", 0, []
    return "\n".join(lines).strip() + "\n", len(pids), pids
