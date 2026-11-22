"""Water 连接器：即刻（Jike）自包含闭环实现。

零外部 Skill 依赖（仅使用 Python 标准库发起请求；若本机有 `qrcode` 库则自动弹窗/打印二维码）：
1. 未登录时支持 `login_qr()` 一键生成二维码（macOS 自动弹窗 + 终端输出扫码链接）并轮询确认；
2. 凭证保存在 `~/.water/jike.json`（同时兼容读取 `~/.jike/config.json`），不进 Git；
3. Token 过期自动刷新，拉取个人收藏精华并脱敏，直接喂给 Tree `seed/staging/`。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

CONNECTOR_ID = "jike"
API_BASE = "https://api.ruguoapp.com"
WATER_CONFIG_DIR = Path.home() / ".water"
TOKEN_FILE = WATER_CONFIG_DIR / "jike.json"


def _load_tokens() -> dict[str, Any]:
    candidates = [
        TOKEN_FILE,
        Path.home() / ".jike/config.json",
        Path.home() / "Desktop/jike/config.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("access_token"):
                    return {
                        "access_token": data["access_token"],
                        "refresh_token": data.get("refresh_token", ""),
                        "device_id": data.get("device_id") or str(uuid.uuid4()),
                        "source_path": str(p),
                    }
            except Exception:
                continue
    return {
        "access_token": os.environ.get("JIKE_ACCESS_TOKEN", ""),
        "refresh_token": os.environ.get("JIKE_REFRESH_TOKEN", ""),
        "device_id": str(uuid.uuid4()),
        "source_path": "",
    }


def _save_tokens(access_token: str, refresh_token: str, device_id: str) -> Path:
    WATER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "device_id": device_id,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    TOKEN_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except Exception:
        pass
    return TOKEN_FILE


def _http_json(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: int = 12,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw}
        return e.code, parsed


def _base_headers(access_token: str = "", device_id: str = "") -> dict[str, str]:
    h = {
        "platform": "web",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "origin": "https://web.okjike.com",
        "referer": "https://web.okjike.com/",
    }
    if access_token:
        h["x-jike-access-token"] = access_token
    if device_id:
        h["x-jike-device-id"] = device_id
    return h


def _refresh_access_token(tokens: dict[str, Any]) -> bool:
    rt = tokens.get("refresh_token")
    if not rt:
        return False
    status, data = _http_json(
        "POST",
        f"{API_BASE}/app_auth_tokens.refresh",
        headers={**_base_headers(device_id=tokens["device_id"]), "x-jike-refresh-token": rt},
        body={},
    )
    if status == 200 and data.get("x-jike-access-token"):
        tokens["access_token"] = data["x-jike-access-token"]
        tokens["refresh_token"] = data.get("x-jike-refresh-token", rt)
        _save_tokens(tokens["access_token"], tokens["refresh_token"], tokens["device_id"])
        return True
    return False


def login_qr(timeout: int = 180) -> bool:
    """自包含扫码登录闭环：创建会话 -> 展示二维码 -> 轮询确认 -> 持久化 Token。"""
    device_id = str(uuid.uuid4())
    headers = _base_headers(device_id=device_id)
    status, session_data = _http_json("POST", f"{API_BASE}/sessions.create", headers=headers, body={})
    session_uuid = session_data.get("uuid") if status == 200 else None
    if not session_uuid:
        print(f"❌ 创建即刻登录会话失败 (HTTP {status}): {session_data}", file=sys.stderr)
        return False

    scan_url = f"https://www.okjike.com/account/scan?uuid={session_uuid}"
    qr_payload = f"jike://page.jk/web?url={urllib.parse.quote(scan_url)}&displayHeader=false&displayFooter=false"

    print("\n请使用【即刻 App】扫描二维码登录（仅保存在本机 ~/.water/jike.json）：")
    qr_img_path: Path | None = None
    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_payload)
        qr.print_ascii(invert=True)

        tmp = Path(tempfile.gettempdir()) / "water_jike_qr.png"
        qrcode.make(qr_payload).save(str(tmp))
        qr_img_path = tmp
        if sys.platform == "darwin":
            subprocess.run(["open", str(tmp)], stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass

    print(f"扫码链接: {scan_url}")
    if qr_img_path:
        print(f"二维码图片: {qr_img_path}")
    print(f"等待手机确认中（超时 {timeout}s）...")
    sys.stdout.flush()

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        q_url = f"{API_BASE}/sessions.wait_for_confirmation?{urllib.parse.urlencode({'uuid': session_uuid})}"
        p_status, data = _http_json("GET", q_url, headers=headers)
        if p_status == 200 and data.get("confirmed"):
            raw_ref = data.get("x-jike-refresh-token", "")
            access = data.get("token", "")
            refresh = raw_ref
            if raw_ref:
                r_status, r_data = _http_json(
                    "POST",
                    f"{API_BASE}/app_auth_tokens.refresh",
                    headers={"x-jike-refresh-token": raw_ref, "x-jike-device-id": device_id},
                    body={},
                )
                if r_status == 200 and r_data.get("x-jike-access-token"):
                    access = r_data["x-jike-access-token"]
                    refresh = r_data.get("x-jike-refresh-token", raw_ref)
            saved = _save_tokens(access, refresh, device_id)
            print(f"✅ 即刻登录成功，凭证已保存至 {saved}")
            return True

    print("❌ 登录超时，请重试。", file=sys.stderr)
    return False


def discover() -> dict[str, Any]:
    tokens = _load_tokens()
    if tokens["access_token"]:
        src = tokens["source_path"] or "环境变量"
        return {
            "name": "即刻 (收藏与观点精华)",
            "available": True,
            "detail": f"已登录（凭证来源: {src}）",
        }
    return {
        "name": "即刻 (收藏与观点精华)",
        "available": False,
        "detail": "未登录，可运行 `water login jike` 一键扫码登录",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    tokens = _load_tokens()
    if not tokens["access_token"]:
        return "", 0, []

    redact = helpers.get("redact_secrets", lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    target = max(5, min(max_sessions, 40))
    items: list[dict[str, Any]] = []
    load_more_key: Any = None
    refreshed = False

    while len(items) < target:
        payload = {"limit": min(20, target - len(items)), "loadMoreKey": load_more_key}
        status, res = _http_json(
            "POST",
            f"{API_BASE}/1.0/collections/list",
            headers=_base_headers(tokens["access_token"], tokens["device_id"]),
            body=payload,
        )
        if status == 401 and not refreshed:
            refreshed = True
            if _refresh_access_token(tokens):
                continue
            break
        if status != 200:
            break
        batch = res.get("data") or []
        if not batch:
            break
        items.extend(batch)
        load_more_key = res.get("loadMoreKey")
        if not load_more_key:
            break

    if not items:
        return "", 0, []

    lines = [
        "# 即刻个人收藏精华（待蒸馏进 01-关于我 / 02-我的思考）",
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
        header = f"### [{created or '未知日期'}] {user}"
        if topic:
            header += f" · #{topic}"
        lines.append(header)
        if content:
            lines.append(redact(content))
        if link:
            lines.append(f"- 外链: {link}")
        if pid:
            lines.append(f"- 原帖: https://web.okjike.com/originalPost/{pid}")
            pids.append(short_hash(f"jike:{pid}"))
        lines.append("")

    return "\n".join(lines).strip() + "\n", len(pids), pids
