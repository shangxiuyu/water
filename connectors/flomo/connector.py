"""Tree Seed 按需连接器：flomo 浮墨笔记（OAuth 2.1 + PKCE + 官方 MCP，纯标准库零依赖）。"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "flomo"
CONNECTOR_NAME = "flomo 浮墨笔记 (卡片/标签/记忆)"
PKG_DIR = Path(__file__).resolve().parent


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    import flomo_client  # type: ignore

    return flomo_client


def _mtime_iso(p: Path) -> str:
    try:
        return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return ""


def discover() -> dict[str, Any]:
    client = _load_client()
    creds = client.load_creds()
    src_p = Path(creds["source_path"]) if (creds.get("source_path") and not str(creds["source_path"]).startswith("env:")) else None
    latest = _mtime_iso(src_p) if (src_p and src_p.is_file()) else ""
    has_tok = bool(creds.get("access_token"))
    return {
        "id": CONNECTOR_ID,
        "name": CONNECTOR_NAME,
        "available": has_tok,
        "needs_login": not has_tok,
        "memory_files": [],
        "chat_count": 1 if has_tok else 0,
        "unit_label": "已登录" if has_tok else "可授权",
        "latest_date": latest,
        "detail": "" if has_tok else "未登录（需 Max/领航者会员），请运行 `tree seed login flomo`",
    }


def login_qr(timeout: int = 180, api_key: str = "") -> bool:
    client = _load_client()
    return bool(client.login_oauth(timeout=timeout, api_key=api_key))


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client = _load_client()
    creds = client.load_creds()
    if not creds.get("access_token"):
        return "", 0, []

    try:
        sid = client.open_mcp_session(creds)
    except Exception:
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# flomo 浮墨笔记个人上下文与近期卡片（待蒸馏进 02-我的思考 / seed/外部上下文源.md）",
        f"> 提取时间: {now_iso()} | 回溯窗口: 近 {days or 60} 天 | 仅作蒸馏参考，不搬运流水",
        "",
    ]
    pids: list[str] = []
    req_id = 2

    # 1. Official user memory & recent context from flomo
    for tool_name, heading in (
        ("memory_user", "一、flomo 提炼的用户稳定画像 (`memory_user`)"),
        ("memory_context", "二、flomo 近期记忆 (`memory_context`)"),
    ):
        data, sid = client.call_tool(creds, sid, req_id, tool_name, {})
        req_id += 1
        if data:
            txt = json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data
            if txt.strip() and "尚未生成" not in txt:
                lines.append(f"## {heading}")
                lines.append(redact(txt.strip()[:2500]))
                lines.append("")
                pids.append(short_hash(f"flomo:{tool_name}:{len(txt)}"))

    # 2. Tag tree
    tag_data, sid = client.call_tool(creds, sid, req_id, "tag_tree", {})
    req_id += 1
    if tag_data:
        tag_txt = json.dumps(tag_data, ensure_ascii=False) if not isinstance(tag_data, str) else tag_data
        lines.append("## 三、flomo 标签树索引（建议登记入 `seed/外部上下文源.md`）")
        lines.append(redact(tag_txt.strip()[:3000]))
        lines.append("")
        pids.append(short_hash(f"flomo:tag_tree:{len(tag_txt)}"))

    # 3. Recent memos
    limit = max(5, min(max_sessions, 50))
    search_args: dict[str, Any] = {"limit": limit}
    if days > 0:
        search_args["start_date"] = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    memos_data, sid = client.call_tool(creds, sid, req_id, "memo_search", search_args)
    req_id += 1

    memo_list: list[dict[str, Any]] = []
    if isinstance(memos_data, dict):
        raw_m = memos_data.get("memos") or memos_data.get("items") or memos_data.get("data") or []
        if isinstance(raw_m, list):
            memo_list = [m for m in raw_m if isinstance(m, dict)]
    elif isinstance(memos_data, list):
        memo_list = [m for m in memos_data if isinstance(m, dict)]

    trunc_ids = [
        str(m.get("id") or m.get("slug") or "")
        for m in memo_list
        if m.get("content_truncated") and (m.get("id") or m.get("slug"))
    ]
    full_map: dict[str, str] = {}
    if trunc_ids:
        batch_data, sid = client.call_tool(creds, sid, req_id, "memo_batch_get", {"ids": trunc_ids[:10]})
        req_id += 1
        if isinstance(batch_data, dict):
            b_list = batch_data.get("memos") or batch_data.get("items") or []
            if isinstance(b_list, list):
                for bm in b_list:
                    if isinstance(bm, dict) and bm.get("id"):
                        full_map[str(bm["id"])] = str(bm.get("content") or "")

    if memo_list:
        lines.append(f"## 四、近期笔记卡片（共 {len(memo_list)} 条）")
        for m in memo_list:
            mid = str(m.get("id") or m.get("slug") or "")
            created = str(m.get("created_at") or m.get("updated_at") or "")[:10]
            content = full_map.get(mid) or str(m.get("content") or "")
            content = re.sub(r"<[^>]+>", " ", content).strip()
            if not content:
                continue
            tags = m.get("tags") if isinstance(m.get("tags"), list) else []
            tag_str = (" · " + " ".join(f"#{t}" for t in tags[:4])) if tags else ""
            lines.append(f"- **[{created or '近期'}]** (`{mid}`){tag_str}：{redact(content[:600])}")
            pids.append(short_hash(f"flomo:memo:{mid or content[:24]}"))
        lines.append("")
    elif isinstance(memos_data, str) and memos_data.strip():
        lines.append("## 四、近期笔记卡片")
        lines.append(redact(memos_data.strip()[:6000]))
        pids.append(short_hash(f"flomo:text:{len(memos_data)}"))

    if not pids:
        return "", 0, []
    return "\n".join(lines).strip() + "\n", len(pids), pids
