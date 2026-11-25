"""Self-contained GetNote (得到大脑 / biji.com) client (100% Python stdlib, zero external dependencies)."""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

GETNOTE_API_BASE = "https://openapi.biji.com/open/api/v1"
GETNOTE_DEFAULT_OAUTH_CLIENT_ID = "cli_a1b2c3d4e5f6789012345678abcdef90"


def cred_file() -> Path:
    return Path.home() / ".tree/credentials/getnote.json"


def load_creds() -> dict[str, Any]:
    env_key = os.environ.get("GETNOTE_API_KEY", "").strip()
    env_cid = os.environ.get("GETNOTE_CLIENT_ID", "").strip()
    if env_key:
        return {
            "api_key": env_key,
            "client_id": env_cid or GETNOTE_DEFAULT_OAUTH_CLIENT_ID,
            "source_path": "env:GETNOTE_API_KEY",
        }
    candidates = [
        cred_file(),
        Path.home() / ".getnote/config.json",
        Path.home() / ".water/getnote.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("api_key"):
                    cid = str(data.get("client_id") or "").strip()
                    if not cid or cid == "getnote-cli":
                        cid = GETNOTE_DEFAULT_OAUTH_CLIENT_ID
                    return {
                        "api_key": str(data["api_key"]).strip(),
                        "client_id": cid,
                        "expires_at": data.get("expires_at", 0),
                        "source_path": str(p),
                    }
            except Exception:
                continue
    return {
        "api_key": "",
        "client_id": GETNOTE_DEFAULT_OAUTH_CLIENT_ID,
        "source_path": "",
    }


def save_creds(api_key: str, client_id: str, expires_at: int = 0) -> Path:
    dest = cred_file()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "api_key": api_key,
        "client_id": client_id or GETNOTE_DEFAULT_OAUTH_CLIENT_ID,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if expires_at:
        payload["expires_at"] = expires_at
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except Exception:
        pass
    # Also sync to ~/.getnote/config.json if not present so @getnote/cli / @getnote/mcp work out of the box
    cli_cfg = Path.home() / ".getnote/config.json"
    if not cli_cfg.is_file():
        try:
            cli_cfg.parent.mkdir(parents=True, exist_ok=True)
            cli_cfg.write_text(
                json.dumps(
                    {"api_key": api_key, "client_id": client_id or GETNOTE_DEFAULT_OAUTH_CLIENT_ID},
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            cli_cfg.chmod(0o600)
        except Exception:
            pass
    return dest


def http_json(
    method: str,
    path: str,
    creds: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 15,
) -> tuple[int, dict[str, Any]]:
    base = (os.environ.get("GETNOTE_API_URL") or GETNOTE_API_BASE).rstrip("/")
    if not base.endswith("/open/api/v1"):
        base = f"{base}/api/v1" if base.endswith("/open") else f"{base}/open/api/v1"
    url = f"{base}/{path.lstrip('/')}"
    if params:
        clean_p = {k: str(v) for k, v in params.items() if v is not None and v != ""}
        if clean_p:
            url = f"{url}?{urllib.parse.urlencode(clean_p)}"

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Tree-Water-GetNote/2.0",
    }
    if creds and creds.get("api_key"):
        headers["Authorization"] = f"Bearer {creds['api_key']}"
        headers["X-Client-ID"] = str(creds.get("client_id") or GETNOTE_DEFAULT_OAUTH_CLIENT_ID)

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
    except Exception as e:
        return 0, {"error": str(e)}


# ---------------------------------------------------------------------------
# Pure-Stdlib QR PNG Fallback Renderer (Version 2-L / 4-L / 6-L, Byte Mode)
# ---------------------------------------------------------------------------

_QR_GF_EXP = [0] * 512
_QR_GF_LOG = [0] * 256
_qr_x = 1
for _qr_i in range(255):
    _QR_GF_EXP[_qr_i] = _qr_x
    _QR_GF_LOG[_qr_x] = _qr_i
    _qr_x <<= 1
    if _qr_x & 0x100:
        _qr_x ^= 0x11D
for _qr_i in range(255, 512):
    _QR_GF_EXP[_qr_i] = _QR_GF_EXP[_qr_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _QR_GF_EXP[_QR_GF_LOG[a] + _QR_GF_LOG[b]]


def _rs_encode(data: list[int], ec_len: int) -> list[int]:
    gen = [1]
    for i in range(ec_len):
        nxt = [0] * (len(gen) + 1)
        factor = _QR_GF_EXP[i]
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _gf_mul(g, factor)
        gen = nxt
    res = list(data) + [0] * ec_len
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data) :]


def render_qr_png(text: str, out_path: Path, scale: int = 10, border: int = 4) -> Path:
    """Pure-Python standard-library QR Code PNG generator (Error Correction L, Mask 0)."""
    payload_bytes = text.encode("utf-8")
    specs = [
        (2, 34, 10, 1, 34, 0, 0, [6, 18]),
        (4, 80, 20, 1, 80, 0, 0, [6, 26]),
        (6, 136, 18, 4, 34, 0, 0, [6, 34]),
        (8, 194, 24, 2, 97, 0, 0, [6, 24, 42]),
    ]
    chosen = None
    for sp in specs:
        if len(payload_bytes) + 2 <= sp[1]:
            chosen = sp
            break
    if chosen is None:
        raise ValueError(f"URL too long for built-in QR encoder ({len(payload_bytes)} bytes)")

    ver, data_cap, ec_len, nb1, dc1, nb2, dc2, aligns = chosen
    bits: list[int] = [0, 1, 0, 0]
    for shift in range(7, -1, -1):
        bits.append((len(payload_bytes) >> shift) & 1)
    for b in payload_bytes:
        for shift in range(7, -1, -1):
            bits.append((b >> shift) & 1)
    bits.extend([0] * min(4, data_cap * 8 - len(bits)))
    while len(bits) % 8 != 0:
        bits.append(0)
    codewords = [
        sum(bits[i + j] << (7 - j) for j in range(8))
        for i in range(0, len(bits), 8)
    ]
    pad_seq = (0xEC, 0x11)
    idx = 0
    while len(codewords) < data_cap:
        codewords.append(pad_seq[idx % 2])
        idx += 1

    blocks_data: list[list[int]] = []
    blocks_ec: list[list[int]] = []
    pos = 0
    for count, dlen in ((nb1, dc1), (nb2, dc2)):
        for _ in range(count):
            blk = codewords[pos : pos + dlen]
            pos += dlen
            blocks_data.append(blk)
            blocks_ec.append(_rs_encode(blk, ec_len))

    final_cw: list[int] = []
    for col in range(max(dc1, dc2)):
        for blk in blocks_data:
            if col < len(blk):
                final_cw.append(blk[col])
    for col in range(ec_len):
        for ec in blocks_ec:
            final_cw.append(ec[col])

    size = ver * 4 + 17
    modules: list[list[int]] = [[0] * size for _ in range(size)]
    is_func = [[False] * size for _ in range(size)]

    def set_func(r: int, c: int, val: int) -> None:
        if 0 <= r < size and 0 <= c < size:
            modules[r][c] = val
            is_func[r][c] = True

    def place_finder(top: int, left: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                val = 1 if (0 <= dr <= 6 and 0 <= dc <= 6 and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))) else 0
                set_func(top + dr, left + dc, val)

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    num_al = len(aligns)
    for i in range(num_al):
        for j in range(num_al):
            if (i == 0 and j == 0) or (i == 0 and j == num_al - 1) or (i == num_al - 1 and j == 0):
                continue
            r, c = aligns[i], aligns[j]
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    set_func(r + dr, c + dc, 1 if max(abs(dr), abs(dc)) in (0, 2) else 0)

    for i in range(8, size - 8):
        if not is_func[6][i]:
            set_func(6, i, 1 if i % 2 == 0 else 0)
        if not is_func[i][6]:
            set_func(i, 6, 1 if i % 2 == 0 else 0)

    for i in range(9):
        if i != 6:
            set_func(8, i, 0)
            set_func(i, 8, 0)
    for i in range(8):
        set_func(8, size - 1 - i, 0)
        set_func(size - 1 - i, 8, 0)
    set_func(size - 8, 8, 1)

    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vbits = (ver << 12) | rem
        for i in range(18):
            bit = (vbits >> i) & 1
            a, b = size - 11 + (i % 3), i // 3
            set_func(b, a, bit)
            set_func(a, b, bit)

    data_bits = [(cw >> s) & 1 for cw in final_cw for s in range(7, -1, -1)]
    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not is_func[r][c]:
                    bval = data_bits[bit_idx] if bit_idx < len(data_bits) else 0
                    bit_idx += 1
                    if (r + c) % 2 == 0:
                        bval ^= 1
                    modules[r][c] = bval
        upward = not upward
        col -= 2

    data_5 = 0b01000
    rem = data_5
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    fmt_bits = ((data_5 << 10) | rem) ^ 0x5412

    for i in range(6):
        modules[i][8] = (fmt_bits >> i) & 1
    modules[7][8] = (fmt_bits >> 6) & 1
    modules[8][8] = (fmt_bits >> 7) & 1
    modules[8][7] = (fmt_bits >> 8) & 1
    for i in range(9, 15):
        modules[8][14 - i] = (fmt_bits >> i) & 1
    for i in range(8):
        modules[8][size - 1 - i] = (fmt_bits >> i) & 1
    for i in range(8, 15):
        modules[size - 15 + i][8] = (fmt_bits >> i) & 1

    img_dim = (size + border * 2) * scale
    raw_rows = bytearray()
    white_row = b"\x00" + (b"\xff" * img_dim)
    for _ in range(border * scale):
        raw_rows.extend(white_row)
    for r in range(size):
        line = bytearray(b"\x00")
        line.extend(b"\xff" * (border * scale))
        for c in range(size):
            line.extend((b"\x00" if modules[r][c] else b"\xff") * scale)
        line.extend(b"\xff" * (border * scale))
        for _ in range(scale):
            raw_rows.extend(line)
    for _ in range(border * scale):
        raw_rows.extend(white_row)

    def _png_chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", img_dim, img_dim, 8, 0, 0, 0, 0)
    png_bytes = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(bytes(raw_rows), 9)) + _png_chunk(b"IEND", b"")
    out_path.write_bytes(png_bytes)
    return out_path


def open_qr_image_window(img_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# High-Level GetNote API & OAuth Device Flow
# ---------------------------------------------------------------------------

def login_oauth(timeout: int = 180, api_key: str = "", client_id: str = "") -> bool:
    """Authenticate with GetNote (得到大脑 / biji.com) via OAuth 2.0 Device Flow or direct API Key."""
    if api_key.strip():
        saved = save_creds(api_key.strip(), client_id.strip() or GETNOTE_DEFAULT_OAUTH_CLIENT_ID)
        print(f"✅ 得到大脑 API Key 已保存至 {saved}")
        return True

    cid = client_id.strip() or os.environ.get("GETNOTE_CLIENT_ID", "").strip() or GETNOTE_DEFAULT_OAUTH_CLIENT_ID
    status, resp = http_json("POST", "/oauth/device/code", body={"client_id": cid})
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    device_code = str(data.get("code") or "").strip()
    verify_uri = str(data.get("verification_uri") or "").strip()
    user_code = str(data.get("user_code") or "").strip()
    qr_data_uri = str(data.get("verification_uri_qrcode") or "").strip()
    interval = max(3, int(data.get("interval") or 5))
    expires_in = min(max(30, timeout), int(data.get("expires_in") or 600))

    if status != 200 or not resp.get("success") or not device_code or not verify_uri:
        print(f"❌ 创建得到大脑 OAuth 授权会话失败 (HTTP {status}): {resp}", file=sys.stderr)
        return False

    qr_img_path = Path(tempfile.gettempdir()) / "tree_getnote_qr.png"
    wrote_qr = False
    try:
        if qr_data_uri.startswith("data:image/png;base64,"):
            b64_part = qr_data_uri.split(",", 1)[1]
            qr_img_path.write_bytes(base64.b64decode(b64_part))
            wrote_qr = True
        else:
            render_qr_png(verify_uri, qr_img_path, scale=10, border=4)
            wrote_qr = True
    except Exception:
        wrote_qr = False

    if wrote_qr:
        open_qr_image_window(qr_img_path)

    print("\n🧠 正在连接【得到大脑（Get笔记 · biji.com）】（凭证仅保存在本机 ~/.tree/credentials/getnote.json）：")
    print(f"   🔗 浏览器或手机扫码授权地址: {verify_uri}")
    if user_code:
        print(f"   🔑 页面确认码 (User Code):  {user_code}")
    if wrote_qr:
        print(f"   📱 高清授权二维码已弹出:    {qr_img_path}")
    print(f"   ⏳ 等待确认授权中（超时 {expires_in}s）...")
    sys.stdout.flush()

    deadline = time.time() + expires_in
    try:
        while time.time() < deadline:
            time.sleep(interval)
            p_status, p_resp = http_json(
                "POST",
                "/oauth/token",
                body={"grant_type": "device_code", "client_id": cid, "code": device_code},
            )
            raw_dump = json.dumps(p_resp, ensure_ascii=False)
            if "authorization_pending" in raw_dump:
                continue
            if "rejected" in raw_dump:
                print("❌ 用户已拒绝得到大脑授权。", file=sys.stderr)
                return False
            if "expired_token" in raw_dump or "already_consumed" in raw_dump:
                print("❌ 授权码已过期或已使用，请重新运行 `tree seed login getnote`。", file=sys.stderr)
                return False
            t_data = p_resp.get("data") if isinstance(p_resp.get("data"), dict) else {}
            got_key = str(t_data.get("api_key") or "").strip()
            got_cid = str(t_data.get("client_id") or cid).strip()
            exp_at = int(t_data.get("expires_at") or 0)
            if p_status == 200 and p_resp.get("success") and got_key:
                saved = save_creds(got_key, got_cid, expires_at=exp_at)
                print(f"✅ 得到大脑授权成功！凭证已保存至 {saved}（并同步兼容 ~/.getnote/config.json）")
                return True
    finally:
        qr_img_path.unlink(missing_ok=True)

    print("❌ 得到大脑登录授权超时，请重试。", file=sys.stderr)
    return False


def list_knowledge_bases(creds: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    owned: list[dict[str, Any]] = []
    subscribed: list[dict[str, Any]] = []
    for scope, scope_cn in (
        ("DEFAULT", "个人知识库"),
        ("BOOKSPACE", "书籍知识库"),
        ("TEAMSPACE", "团队知识库"),
        ("CUSTOMER", "客户知识库"),
    ):
        st, resp = http_json(
            "GET",
            "/resource/knowledge/list",
            creds=creds,
            params={"scope": scope, "page": 1, "page_size": 30},
        )
        if st == 200 and resp.get("success"):
            data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
            for t in (data.get("topics") or []):
                if isinstance(t, dict):
                    item = dict(t)
                    item["_scope"] = scope
                    item["_scope_cn"] = scope_cn
                    owned.append(item)

    st_sub, resp_sub = http_json(
        "GET",
        "/resource/knowledge/subscribe/list",
        creds=creds,
        params={"scope": "DEFAULT", "page": 1, "page_size": 20},
    )
    if st_sub == 200 and resp_sub.get("success"):
        s_data = resp_sub.get("data") if isinstance(resp_sub.get("data"), dict) else {}
        for t in (s_data.get("topics") or []):
            if isinstance(t, dict):
                subscribed.append(dict(t))
    return owned, subscribed


def list_sprouts(creds: dict[str, Any]) -> list[dict[str, Any]]:
    now_dt = datetime.datetime.now().astimezone()
    prev_dt = now_dt.replace(day=1) - datetime.timedelta(days=1)
    items: list[dict[str, Any]] = []
    for m_str in dict.fromkeys([now_dt.strftime("%Y-%m"), prev_dt.strftime("%Y-%m")]):
        st, resp = http_json("GET", "/resource/note/sprouts", creds=creds, params={"month": m_str, "limit": 5})
        if st == 200 and resp.get("success"):
            sp_data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
            for sp in (sp_data.get("sprouts") or [])[:5]:
                if isinstance(sp, dict):
                    item = dict(sp)
                    item.setdefault("_month", m_str)
                    items.append(item)
    return items


def list_notes(creds: dict[str, Any], days: int = 60, limit: int = 40) -> list[dict[str, Any]]:
    now_dt = datetime.datetime.now().astimezone()
    params: dict[str, Any] = {"page_size": max(1, min(limit, 50))}
    if days > 0:
        params["created_from"] = (now_dt - datetime.timedelta(days=days)).isoformat(timespec="seconds")

    st, resp = http_json("GET", "/resource/note/list", creds=creds, params=params)
    notes: list[dict[str, Any]] = []
    if st == 200 and resp.get("success"):
        n_data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        notes.extend(n for n in (n_data.get("notes") or []) if isinstance(n, dict))

    if not notes and "created_from" in params:
        st2, resp2 = http_json("GET", "/resource/note/list", creds=creds, params={"page_size": max(1, min(limit, 30))})
        if st2 == 200 and resp2.get("success"):
            n_data2 = resp2.get("data") if isinstance(resp2.get("data"), dict) else {}
            notes.extend(n for n in (n_data2.get("notes") or []) if isinstance(n, dict))
    return notes[:limit]


def get_note_detail(creds: dict[str, Any], note_id: str) -> dict[str, Any]:
    st, resp = http_json("GET", "/resource/note/detail", creds=creds, params={"id": note_id.strip()})
    if st == 200 and resp.get("success"):
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        note = data.get("note")
        if isinstance(note, dict):
            return note
    raise RuntimeError(f"获取笔记 `{note_id}` 失败 (HTTP {st}): {resp}")


def recall(creds: dict[str, Any], query: str, top_k: int = 5) -> list[dict[str, Any]]:
    st, resp = http_json(
        "POST",
        "/resource/recall",
        creds=creds,
        body={"query": query.strip(), "top_k": max(1, min(top_k, 20))},
    )
    if st == 200 and resp.get("success"):
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        results = data.get("results")
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
    raise RuntimeError(f"语义召回 `{query}` 失败 (HTTP {st}): {resp}")


def _cli_main() -> None:
    parser = argparse.ArgumentParser(description="GetNote (得到大脑) 只读查询 CLI")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="查看得到大脑授权状态")
    sub.add_parser("kbs", help="列出全部自有与订阅知识库")

    p_notes = sub.add_parser("notes", help="列出近期笔记与划线")
    p_notes.add_argument("--days", type=int, default=60)
    p_notes.add_argument("--limit", type=int, default=20)

    p_detail = sub.add_parser("detail", help="读取单篇笔记完整正文/划线/录音转写")
    p_detail.add_argument("note_id", help="笔记 ID")

    p_recall = sub.add_parser("recall", help="语义召回得到大脑相关笔记")
    p_recall.add_argument("query", help="检索关键词或自然语言问题")
    p_recall.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()
    creds = load_creds()
    if args.cmd == "status":
        print(json.dumps({"authenticated": bool(creds.get("api_key")), "source": creds.get("source_path") or ""}, ensure_ascii=False, indent=2))
        return
    if not creds.get("api_key"):
        print("❌ 得到大脑尚未登录，请先运行: tree seed login getnote", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "kbs":
        owned, subscribed = list_knowledge_bases(creds)
        for t in owned:
            tid = t.get("id") or t.get("topic_id") or ""
            print(f"- [{t.get('_scope_cn')}] {t.get('name') or t.get('title')} (topic_id: {tid})")
        for t in subscribed:
            tid = t.get("id") or t.get("topic_id") or ""
            print(f"- [订阅知识库] {t.get('name') or t.get('title')} (topic_id: {tid})")
    elif args.cmd == "notes":
        for n in list_notes(creds, days=args.days, limit=args.limit):
            nid = n.get("id") or n.get("note_id") or ""
            title = n.get("display_title") or n.get("title") or "无标题笔记"
            created = str((n.get("timestamps") or {}).get("created_at") or n.get("created_at") or "")[:10]
            print(f"- [{created or '近期'}] {title} (ID: {nid})")
    elif args.cmd == "detail":
        note = get_note_detail(creds, args.note_id)
        print(json.dumps(note, ensure_ascii=False, indent=2))
    elif args.cmd == "recall":
        results = recall(creds, args.query, top_k=args.top_k)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
