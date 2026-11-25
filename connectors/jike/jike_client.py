"""Self-contained Jike (即刻) client (100% Python stdlib, QR code login & collection reader)."""

from __future__ import annotations

import argparse
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
import uuid
import zlib
from pathlib import Path
from typing import Any

JIKE_API_BASE = "https://api.ruguoapp.com"


def cred_file() -> Path:
    return Path.home() / ".tree/credentials/jike.json"


def load_tokens() -> dict[str, Any]:
    candidates = [
        cred_file(),
        Path.home() / ".water/jike.json",
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


def save_tokens(access_token: str, refresh_token: str, device_id: str) -> Path:
    dest = cred_file()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "device_id": device_id,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except Exception:
        pass
    return dest


def http_json(
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
    except Exception as e:
        return 0, {"error": str(e)}


def jike_headers(access_token: str = "", device_id: str = "") -> dict[str, str]:
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


def refresh_token(tokens: dict[str, Any]) -> bool:
    rt = tokens.get("refresh_token")
    if not rt:
        return False
    status, data = http_json(
        "POST",
        f"{JIKE_API_BASE}/app_auth_tokens.refresh",
        headers={**jike_headers(device_id=tokens["device_id"]), "x-jike-refresh-token": rt},
        body={},
    )
    if status == 200 and data.get("x-jike-access-token"):
        tokens["access_token"] = data["x-jike-access-token"]
        tokens["refresh_token"] = data.get("x-jike-refresh-token", rt)
        save_tokens(tokens["access_token"], tokens["refresh_token"], tokens["device_id"])
        return True
    return False


_QR_VERSIONS_L = [
    (1, 26, 7, 1, 19, 0, 0, []),
    (2, 44, 10, 1, 34, 0, 0, [6, 18]),
    (3, 70, 15, 1, 55, 0, 0, [6, 22]),
    (4, 100, 20, 1, 80, 0, 0, [6, 26]),
    (5, 134, 26, 1, 108, 0, 0, [6, 30]),
    (6, 172, 18, 2, 68, 0, 0, [6, 34]),
    (7, 196, 20, 2, 78, 0, 0, [6, 22, 38]),
    (8, 242, 24, 2, 97, 0, 0, [6, 24, 42]),
    (9, 292, 30, 2, 116, 0, 0, [6, 26, 46]),
    (10, 346, 18, 2, 68, 2, 69, [6, 28, 50]),
]

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_gf_v = 1
for _gf_i in range(255):
    _GF_EXP[_gf_i] = _gf_v
    _GF_LOG[_gf_v] = _gf_i
    _gf_v <<= 1
    if _gf_v & 0x100:
        _gf_v ^= 0x11D
for _gf_i in range(255, 512):
    _GF_EXP[_gf_i] = _GF_EXP[_gf_i - 255]


def _gf_mul(a: int, b: int) -> int:
    return 0 if (a == 0 or b == 0) else _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_encode(data: list[int], nsym: int) -> list[int]:
    gen = [1]
    for i in range(nsym):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _gf_mul(g, _GF_EXP[i])
        gen = nxt
    res = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data) :]


def render_qr_png(text: str, out_path: Path, scale: int = 12, border: int = 4) -> Path:
    raw = text.encode("utf-8")
    chosen = None
    for spec in _QR_VERSIONS_L:
        ver, _tot, _ec, nb1, dc1, nb2, dc2, _al = spec
        data_cap = nb1 * dc1 + nb2 * dc2
        len_bits = 8 if ver <= 9 else 16
        if len(raw) <= (data_cap * 8 - 4 - len_bits) // 8:
            chosen = spec
            break
    if not chosen:
        raise ValueError(f"二维码内容过长 ({len(raw)} bytes)")

    ver, _tot, ec_len, nb1, dc1, nb2, dc2, aligns = chosen
    data_cap = nb1 * dc1 + nb2 * dc2
    len_bits = 8 if ver <= 9 else 16

    bits: list[int] = [0, 1, 0, 0]
    for shift in range(len_bits - 1, -1, -1):
        bits.append((len(raw) >> shift) & 1)
    for b in raw:
        for shift in range(7, -1, -1):
            bits.append((b >> shift) & 1)
    bits.extend([0] * min(4, data_cap * 8 - len(bits)))
    while len(bits) % 8 != 0:
        bits.append(0)
    codewords = [int("".join(str(x) for x in bits[i : i + 8]), 2) for i in range(0, len(bits), 8)]
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


def login_qr(timeout: int = 180) -> bool:
    device_id = str(uuid.uuid4())
    headers = jike_headers(device_id=device_id)
    status, session_data = http_json("POST", f"{JIKE_API_BASE}/sessions.create", headers=headers, body={})
    session_uuid = session_data.get("uuid") if status == 200 else None
    if not session_uuid:
        print(f"❌ 创建即刻登录会话失败 (HTTP {status}): {session_data}", file=sys.stderr)
        return False

    scan_url = f"https://www.okjike.com/account/scan?uuid={session_uuid}"
    qr_payload = f"jike://page.jk/web?url={urllib.parse.quote(scan_url)}&displayHeader=false&displayFooter=false"

    qr_img_path = Path(tempfile.gettempdir()) / "tree_jike_qr.png"
    render_qr_png(qr_payload, qr_img_path, scale=12, border=4)
    open_qr_image_window(qr_img_path)

    print("\n📱 已在屏幕弹出高清登录二维码图片，请使用【即刻 App】扫码确认（凭证仅保存在本机 ~/.tree/credentials/jike.json）：")
    print(f"   二维码图片路径: {qr_img_path}")
    print(f"   等待手机确认中（超时 {timeout}s）...")
    sys.stdout.flush()

    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            time.sleep(2)
            q_url = f"{JIKE_API_BASE}/sessions.wait_for_confirmation?{urllib.parse.urlencode({'uuid': session_uuid})}"
            p_status, data = http_json("GET", q_url, headers=headers)
            if p_status == 200 and data.get("confirmed"):
                raw_ref = data.get("x-jike-refresh-token", "")
                access = data.get("token", "")
                refresh = raw_ref
                if raw_ref:
                    r_status, r_data = http_json(
                        "POST",
                        f"{JIKE_API_BASE}/app_auth_tokens.refresh",
                        headers={"x-jike-refresh-token": raw_ref, "x-jike-device-id": device_id},
                        body={},
                    )
                    if r_status == 200 and r_data.get("x-jike-access-token"):
                        access = r_data["x-jike-access-token"]
                        refresh = r_data.get("x-jike-refresh-token", raw_ref)
                saved = save_tokens(access, refresh, device_id)
                print(f"✅ 即刻登录成功，凭证已保存至 {saved}")
                return True
    finally:
        qr_img_path.unlink(missing_ok=True)

    print("❌ 登录超时，请重试。", file=sys.stderr)
    return False


def fetch_collections(tokens: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
    target = max(1, min(limit, 100))
    items: list[dict[str, Any]] = []
    load_more_key: Any = None
    refreshed = False

    while len(items) < target:
        payload = {"limit": min(20, target - len(items)), "loadMoreKey": load_more_key}
        status, res = http_json(
            "POST",
            f"{JIKE_API_BASE}/1.0/collections/list",
            headers=jike_headers(tokens["access_token"], tokens["device_id"]),
            body=payload,
        )
        if status == 401 and not refreshed:
            refreshed = True
            if refresh_token(tokens):
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
    return items


def _cli_main() -> None:
    parser = argparse.ArgumentParser(description="Jike (即刻) 只读查询 CLI")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="查看即刻登录状态")
    p_col = sub.add_parser("collections", help="读取即刻个人精选收藏")
    p_col.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    tokens = load_tokens()
    if args.cmd == "status":
        print(json.dumps({"authenticated": bool(tokens.get("access_token")), "source": tokens.get("source_path") or ""}, ensure_ascii=False, indent=2))
        return
    if not tokens.get("access_token"):
        print("❌ 即刻尚未登录，请先运行: tree seed login jike", file=sys.stderr)
        sys.exit(1)
    if args.cmd == "collections":
        print(json.dumps(fetch_collections(tokens, limit=args.limit), ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
