"""Self-contained flomo (浮墨笔记) MCP client (100% Python stdlib, OAuth 2.1 + PKCE)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

FLOMO_OAUTH_REGISTER_URL = "https://flomoapp.com/oauth/register"
FLOMO_OAUTH_AUTH_URL = "https://flomoapp.com/integration/grant"
FLOMO_OAUTH_TOKEN_URL = "https://flomoapp.com/oauth/token"
FLOMO_MCP_URL = "https://flomoapp.com/mcp"


def cred_file() -> Path:
    return Path.home() / ".tree/credentials/flomo.json"


def load_creds() -> dict[str, Any]:
    env_tok = os.environ.get("FLOMO_MCP_TOKEN", "").strip()
    if env_tok:
        return {"access_token": env_tok, "refresh_token": "", "client_id": "", "source_path": "env:FLOMO_MCP_TOKEN"}
    p = cred_file()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("access_token"):
                return {
                    "access_token": str(data["access_token"]).strip(),
                    "refresh_token": str(data.get("refresh_token") or "").strip(),
                    "client_id": str(data.get("client_id") or "").strip(),
                    "source_path": str(p),
                }
        except Exception:
            pass
    return {"access_token": "", "refresh_token": "", "client_id": "", "source_path": ""}


def save_creds(access_token: str, refresh_token: str = "", client_id: str = "") -> Path:
    dest = cred_file()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except Exception:
        pass
    return dest


def refresh_token(creds: dict[str, Any]) -> bool:
    rt = creds.get("refresh_token")
    cid = creds.get("client_id")
    if not rt or not cid:
        return False
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid}
    ).encode("utf-8")
    req = urllib.request.Request(
        FLOMO_OAUTH_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if data.get("access_token"):
            creds["access_token"] = data["access_token"]
            creds["refresh_token"] = data.get("refresh_token") or rt
            save_creds(creds["access_token"], creds["refresh_token"], cid)
            return True
    except Exception:
        pass
    return False


def _open_browser_url(url: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def login_oauth(timeout: int = 180, api_key: str = "") -> bool:
    """Authenticate with flomo via OAuth 2.1 + PKCE (or direct MCP Bearer token)."""
    if api_key.strip():
        saved = save_creds(api_key.strip())
        print(f"✅ flomo Token 已保存至 {saved}")
        return True

    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state_val = uuid.uuid4().hex

    result_box: dict[str, str] = {}
    done_ev = threading.Event()

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("error"):
                result_box["error"] = str(qs["error"][0])
                html_msg = "<html><meta charset='utf-8'><body><h2>授权已取消或失败</h2><p>请返回终端查看详情。</p></body></html>"
            elif qs.get("state", [""])[0] != state_val:
                result_box["error"] = "state_mismatch"
                html_msg = "<html><meta charset='utf-8'><body><h2>State 校验失败</h2></body></html>"
            else:
                result_box["code"] = str((qs.get("code") or [""])[0])
                html_msg = "<html><meta charset='utf-8'><body><h2>✅ flomo 授权成功</h2><p>可以关闭此页面，返回终端继续。</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_msg.encode("utf-8"))
            done_ev.set()

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = httpd.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    reg_payload = json.dumps(
        {
            "client_name": "Tree Personal Context OS",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "mcp",
        }
    ).encode("utf-8")
    reg_req = urllib.request.Request(
        FLOMO_OAUTH_REGISTER_URL,
        data=reg_payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(reg_req, timeout=15) as resp:
            reg_data = json.loads(resp.read().decode("utf-8", errors="replace"))
        client_id = str(reg_data.get("client_id") or "").strip()
    except Exception as e:
        httpd.server_close()
        print(f"❌ flomo OAuth 动态注册失败: {e}", file=sys.stderr)
        return False

    if not client_id:
        httpd.server_close()
        print("❌ flomo OAuth 未返回 client_id。", file=sys.stderr)
        return False

    auth_qs = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "mcp",
            "state": state_val,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": FLOMO_MCP_URL,
        }
    )
    auth_url = f"{FLOMO_OAUTH_AUTH_URL}?{auth_qs}"

    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()
    _open_browser_url(auth_url)

    print("\n📝 正在连接【flomo 浮墨笔记】（凭证仅保存在本机 ~/.tree/credentials/flomo.json）：")
    print("   ℹ️ 注：flomo 官方 MCP 需 flomo Max 或领航者会员。")
    print(f"   🔗 浏览器授权地址: {auth_url}")
    print(f"   ⏳ 等待浏览器点击授权（超时 {timeout}s）...")
    sys.stdout.flush()

    try:
        got = done_ev.wait(timeout=max(30, timeout))
    finally:
        httpd.shutdown()
        httpd.server_close()

    if not got or result_box.get("error") or not result_box.get("code"):
        err = result_box.get("error") or "超时未收到回调"
        print(f"❌ flomo 授权未完成: {err}", file=sys.stderr)
        return False

    tok_body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": result_box["code"],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": FLOMO_MCP_URL,
        }
    ).encode("utf-8")
    tok_req = urllib.request.Request(
        FLOMO_OAUTH_TOKEN_URL,
        data=tok_body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(tok_req, timeout=15) as resp:
            tok_data = json.loads(resp.read().decode("utf-8", errors="replace"))
        access_tok = str(tok_data.get("access_token") or "").strip()
        refresh_tok = str(tok_data.get("refresh_token") or "").strip()
        if access_tok:
            saved = save_creds(access_tok, refresh_tok, client_id)
            print(f"✅ flomo 授权成功！凭证已保存至 {saved}")
            return True
    except Exception as e:
        print(f"❌ 换取 flomo Token 失败: {e}", file=sys.stderr)
    return False


def mcp_post(creds: dict[str, Any], payload: dict[str, Any], session_id: str = "") -> tuple[int, dict[str, Any], str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {creds['access_token']}",
        "User-Agent": "Tree-Water-Flomo/2.0",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        FLOMO_MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            sid = resp.headers.get("Mcp-Session-Id") or session_id
            raw = resp.read().decode("utf-8", errors="replace").strip()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in ctype or raw.startswith("event:") or raw.startswith("data:"):
                parsed_obj: dict[str, Any] = {}
                for ln in raw.splitlines():
                    if ln.startswith("data:"):
                        chunk = ln[5:].strip()
                        if chunk:
                            try:
                                parsed_obj = json.loads(chunk)
                            except Exception:
                                pass
                return resp.status, parsed_obj, sid
            return resp.status, (json.loads(raw) if raw else {}), sid
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", errors="replace")}, session_id
    except Exception as e:
        return 0, {"error": str(e)}, session_id


def open_mcp_session(creds: dict[str, Any]) -> str:
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "tree-water-flomo", "version": "2.0"},
        },
    }
    st, _, sid = mcp_post(creds, init_payload)
    if st == 401 and refresh_token(creds):
        st, _, sid = mcp_post(creds, init_payload)
    if st not in (200, 202):
        raise RuntimeError(f"flomo MCP 初始化失败 (HTTP {st})")
    mcp_post(creds, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id=sid)
    return sid


def call_tool(creds: dict[str, Any], session_id: str, req_id: int, tool_name: str, arguments: dict[str, Any]) -> tuple[Any, str]:
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    st, resp, sid = mcp_post(creds, payload, session_id=session_id)
    if st == 401 and refresh_token(creds):
        st, resp, sid = mcp_post(creds, payload, session_id=sid)
    if st != 200 or "error" in resp:
        return None, sid
    res = resp.get("result") if isinstance(resp.get("result"), dict) else {}
    if res.get("structuredContent") is not None:
        return res["structuredContent"], sid
    contents = res.get("content") if isinstance(res.get("content"), list) else []
    texts = [str(c.get("text") or "") for c in contents if isinstance(c, dict) and c.get("type") == "text"]
    joined = "\n".join(t for t in texts if t).strip()
    if joined:
        try:
            return json.loads(joined), sid
        except Exception:
            return joined, sid
    return None, sid


def _cli_main() -> None:
    parser = argparse.ArgumentParser(description="flomo (浮墨笔记) 只读查询 CLI")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="查看 flomo 授权状态")
    sub.add_parser("memory", help="读取 flomo memory_user 与 memory_context")
    sub.add_parser("tags", help="读取 flomo 标签树 (tag_tree)")
    p_search = sub.add_parser("search", help="按关键词/标签检索 flomo 卡片")
    p_search.add_argument("query", nargs="?", default="", help="搜索关键词")
    p_search.add_argument("--tag", default="", help="标签筛选")
    p_search.add_argument("--limit", type=int, default=15)

    args = parser.parse_args()
    creds = load_creds()
    if args.cmd == "status":
        print(json.dumps({"authenticated": bool(creds.get("access_token")), "source": creds.get("source_path") or ""}, ensure_ascii=False, indent=2))
        return
    if not creds.get("access_token"):
        print("❌ flomo 尚未登录，请先运行: tree seed login flomo", file=sys.stderr)
        sys.exit(1)

    sid = open_mcp_session(creds)
    if args.cmd == "memory":
        u_data, sid = call_tool(creds, sid, 2, "memory_user", {})
        c_data, _ = call_tool(creds, sid, 3, "memory_context", {})
        print(json.dumps({"memory_user": u_data, "memory_context": c_data}, ensure_ascii=False, indent=2))
    elif args.cmd == "tags":
        t_data, _ = call_tool(creds, sid, 2, "tag_tree", {})
        print(json.dumps(t_data, ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        q_args: dict[str, Any] = {"limit": args.limit}
        if args.query:
            q_args["query"] = args.query
        if args.tag:
            q_args["tag"] = args.tag
        m_data, _ = call_tool(creds, sid, 2, "memo_search", q_args)
        print(json.dumps(m_data, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
