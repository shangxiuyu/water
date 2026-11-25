"""Tree Seed 按需连接器：得到大脑（Get笔记 · biji.com，纯 Python 标准库零依赖）。"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "getnote"
CONNECTOR_NAME = "得到大脑 (知识库/笔记/划线)"
PKG_DIR = Path(__file__).resolve().parent


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    import getnote_client  # type: ignore

    return getnote_client


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
    has_key = bool(creds.get("api_key"))
    return {
        "id": CONNECTOR_ID,
        "name": CONNECTOR_NAME,
        "available": has_key,
        "needs_login": not has_key,
        "memory_files": [],
        "chat_count": 1 if has_key else 0,
        "unit_label": "已登录" if has_key else "可扫码",
        "latest_date": latest,
        "detail": "" if has_key else "未登录，请运行 `tree seed login getnote`",
    }


def login_qr(timeout: int = 180, api_key: str = "", client_id: str = "") -> bool:
    client = _load_client()
    return bool(client.login_oauth(timeout=timeout, api_key=api_key, client_id=client_id))


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    """
    Read-only extraction from GetNote (得到大脑 / biji.com):
    1. Knowledge base registry (`DEFAULT` / `BOOKSPACE` / `CUSTOMER` / `TEAMSPACE` + subscribed KBs)
       for registering into `seed/外部上下文源.md` without dumping full note bodies.
    2. Recent personal notes, monthly sprout reports (`sprouts`), recorder flashes,
       and Dedao course/ebook highlights for distilling 1% insights into `02` / `03`.
    """
    client = _load_client()
    creds = client.load_creds()
    if not creds.get("api_key"):
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    target_notes = max(5, min(max_sessions, 50))

    lines = [
        "# 得到大脑（Get笔记 · biji.com）外脑知识库目录与近期认知线索",
        f"> 提取时间: {now_iso()} | 回溯窗口: 近 {days or 60} 天 | 铁律：知识库索引登记入 `seed/外部上下文源.md`，已消化结论提炼入 `02-我的思考` / `03-我正在做的事`，严禁将原文全量搬入 Tree",
        "",
    ]
    pids: list[str] = []

    # 1. Knowledge bases (owned & subscribed across scopes)
    owned_kbs, sub_kbs = client.list_knowledge_bases(creds)
    kb_lines: list[str] = []
    for t in owned_kbs:
        tid = str(t.get("id") or t.get("topic_id") or "").strip()
        tname = str(t.get("name") or t.get("title") or "").strip()
        tdesc = str(t.get("description") or "").strip()
        stats = t.get("stats") if isinstance(t.get("stats"), dict) else {}
        ncnt = stats.get("note_count") if stats.get("note_count") is not None else t.get("note_count")
        book_info = t.get("book_info") if isinstance(t.get("book_info"), dict) else {}
        b_author = str(book_info.get("author") or "").strip()
        scope_cn = str(t.get("_scope_cn") or "知识库")
        if not tname:
            continue
        meta_tail = f"（ID: `{tid}`" + (f" · 作者: {b_author}" if b_author else "") + (f" · {ncnt} 篇" if ncnt is not None else "") + "）"
        desc_tail = f" — {redact(tdesc[:100])}" if tdesc else ""
        kb_lines.append(f"- **[{scope_cn}] {redact(tname)}** {meta_tail}{desc_tail}")
        pids.append(short_hash(f"getnote:kb:{tid or tname}"))

    for t in sub_kbs:
        tid = str(t.get("id") or t.get("topic_id") or "").strip()
        tname = str(t.get("name") or t.get("title") or "").strip()
        if tname:
            kb_lines.append(f"- **[订阅知识库] {redact(tname)}**（ID: `{tid}`）")
            pids.append(short_hash(f"getnote:subkb:{tid or tname}"))

    if kb_lines:
        lines.append("## 一、知识库全景索引（建议同步登记至 `seed/外部上下文源.md`）")
        lines.extend(kb_lines)
        lines.append("")

    # 2. Current & previous month Sprout reports (发芽报告)
    sprout_lines: list[str] = []
    for sp in client.list_sprouts(creds):
        sp_id = str(sp.get("id") or sp.get("sprout_id") or "").strip()
        sp_title = str(sp.get("title") or "发芽报告").strip()
        sp_summary = str(sp.get("summary") or sp.get("content") or "").strip()
        sp_date = str(sp.get("created_at") or sp.get("_month") or "")[:10]
        if sp_title or sp_summary:
            sprout_lines.append(
                f"- **[{sp_date}] {redact(sp_title)}**"
                + (f"（ID: `{sp_id}`）" if sp_id else "")
                + (f"：{redact(sp_summary[:240])}" if sp_summary else "")
            )
            pids.append(short_hash(f"getnote:sprout:{sp_id or sp_title}"))

    if sprout_lines:
        lines.append("## 二、近期「发芽报告」认知阶段总结")
        lines.extend(sprout_lines)
        lines.append("")

    # 3. Recent notes & Dedao highlights
    notes = client.list_notes(creds, days=days, limit=target_notes)
    if notes:
        lines.append(f"## 三、近期笔记、闪念与得到读书/课程划线（共 {len(notes)} 条）")
        for n in notes:
            nid = str(n.get("id") or n.get("note_id") or "").strip()
            ntype = str(n.get("note_type") or "note").strip()
            nsrc = str(n.get("source") or "").strip()
            title = str(n.get("display_title") or n.get("title") or "").strip()
            created = str((n.get("timestamps") or {}).get("created_at") or n.get("created_at") or "")[:10]
            content = str(n.get("content") or "").strip()
            preview = str((n.get("preview") or {}).get("text") or "").strip()
            ref_content = str(n.get("ref_content") or "").strip()
            ref_obj = n.get("reference") if isinstance(n.get("reference"), dict) else {}
            ref_title = str(ref_obj.get("title") or (ref_obj.get("article") or {}).get("title") or "").strip()
            ref_author = str(ref_obj.get("author") or "").strip()
            tags_raw = n.get("tags") if isinstance(n.get("tags"), list) else []
            tag_names = [str(t.get("name") or "").strip() for t in tags_raw if isinstance(t, dict) and t.get("name")]

            body_text = content or preview
            if not title and not body_text and not ref_content:
                continue

            src_badge = f"{nsrc}/{ntype}" if nsrc else ntype
            tag_badge = (" · " + " ".join(f"#{t}" for t in tag_names[:4])) if tag_names else ""
            head_title = redact(title or ref_title or "无标题笔记")
            lines.append(f"### [{created or '近期'}] {head_title} (`{src_badge}` · ID: `{nid}`){tag_badge}")
            if ref_title or ref_author:
                lines.append(f"- 出处: 《{redact(ref_title)}》" + (f"（{redact(ref_author)}）" if ref_author else ""))
            if ref_content:
                lines.append(f"- 划线原文: {redact(ref_content[:360])}")
            if body_text:
                lines.append(f"- 笔记/批注: {redact(body_text[:450])}")
            lines.append("")
            pids.append(short_hash(f"getnote:note:{nid or head_title[:24]}"))

    if not pids:
        return "", 0, []
    return "\n".join(lines).strip() + "\n", len(pids), pids
