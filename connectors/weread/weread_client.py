"""Self-contained WeRead (微信读书 Agent Gateway) client (100% Python stdlib)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

WEREAD_GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
WEREAD_SKILL_VERSION = "1.0.3"


def cred_file() -> Path:
    return Path.home() / ".tree/credentials/weread.json"


def load_creds() -> dict[str, Any]:
    env_key = os.environ.get("WEREAD_API_KEY", "").strip()
    if env_key:
        return {"api_key": env_key, "source_path": "env:WEREAD_API_KEY"}
    p = cred_file()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("api_key"):
                return {"api_key": str(data["api_key"]).strip(), "source_path": str(p)}
        except Exception:
            pass
    return {"api_key": "", "source_path": ""}


def save_creds(api_key: str) -> Path:
    dest = cred_file()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"api_key": api_key, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except Exception:
        pass
    return dest


def gateway_call(api_key: str, api_name: str, params: dict[str, Any] | None = None, timeout: int = 15) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {"api_name": api_name, "skill_version": WEREAD_SKILL_VERSION}
    if params:
        body.update(params)
    req = urllib.request.Request(
        WEREAD_GATEWAY_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Tree-Water-WeRead/2.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:
        return 0, {"error": str(e)}


def login_key(api_key: str = "") -> bool:
    """Save and verify WeRead Agent Gateway API Key (`wrk-...`)."""
    key = api_key.strip() or os.environ.get("WEREAD_API_KEY", "").strip()
    if not key:
        print("\n📚 连接【微信读书（WeRead）】需要填入你的微信读书 `WEREAD_API_KEY`（格式 `wrk-xxxxxxxx`）：")
        try:
            key = input("   请粘贴 WEREAD_API_KEY（回车取消）: ").strip()
        except EOFError:
            key = ""
    if not key:
        print("❌ 未提供 WEREAD_API_KEY。也可直接运行：`tree seed login weread --api-key wrk-xxxx`", file=sys.stderr)
        return False

    st, resp = gateway_call(key, "/readdata/detail", {"mode": "overall"})
    if st != 200 or (isinstance(resp.get("errcode"), int) and resp["errcode"] != 0):
        print(f"❌ 微信读书 API Key 校验失败 (HTTP {st}): {resp}", file=sys.stderr)
        return False
    saved = save_creds(key)
    print(f"✅ 微信读书授权校验通过！凭证已保存至 {saved}")
    return True


def _cli_main() -> None:
    parser = argparse.ArgumentParser(description="WeRead (微信读书) 只读查询 CLI")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="查看微信读书 Key 绑定状态")
    sub.add_parser("profile", help="查看阅读统计画像 (/readdata/detail)")
    sub.add_parser("notebooks", help="列出有笔记/想法的书籍清单")

    p_bm = sub.add_parser("bookmarks", help="读取指定书籍的章节划线")
    p_bm.add_argument("book_id", help="书籍 bookId")

    p_rv = sub.add_parser("reviews", help="读取指定书籍的个人想法与书评")
    p_rv.add_argument("book_id", help="书籍 bookId")
    p_rv.add_argument("--count", type=int, default=20)

    args = parser.parse_args()
    creds = load_creds()
    if args.cmd == "status":
        print(json.dumps({"authenticated": bool(creds.get("api_key")), "source": creds.get("source_path") or ""}, ensure_ascii=False, indent=2))
        return
    api_key = creds.get("api_key") or ""
    if not api_key:
        print("❌ 微信读书尚未绑定 API Key，请先运行: tree seed login weread --api-key <wrk-...>", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "profile":
        _, resp = gateway_call(api_key, "/readdata/detail", {"mode": "overall"})
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    elif args.cmd == "notebooks":
        _, resp = gateway_call(api_key, "/user/notebooks", {"count": 30})
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    elif args.cmd == "bookmarks":
        _, resp = gateway_call(api_key, "/book/bookmarklist", {"bookId": args.book_id})
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    elif args.cmd == "reviews":
        _, resp = gateway_call(api_key, "/review/list/mine", {"bookid": args.book_id, "count": args.count, "synckey": 0})
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
