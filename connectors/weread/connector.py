"""Tree Seed 按需连接器：微信读书 WeRead（官方 Agent Gateway，纯标准库零依赖）。"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from typing import Any

CONNECTOR_ID = "weread"
CONNECTOR_NAME = "微信读书 (书架/想法/划线)"
PKG_DIR = Path(__file__).resolve().parent


def _load_client():
    if str(PKG_DIR) not in sys.path:
        sys.path.insert(0, str(PKG_DIR))
    import weread_client  # type: ignore

    return weread_client


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
        "unit_label": "已登录" if has_key else "需Key",
        "latest_date": latest,
        "detail": "" if has_key else "未配置 Key，请运行 `tree seed login weread --api-key <wrk-...>`",
    }


def login_qr(timeout: int = 180, api_key: str = "") -> bool:
    client = _load_client()
    return bool(client.login_key(api_key=api_key))


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    client = _load_client()
    creds = client.load_creds()
    api_key = creds.get("api_key") or ""
    if not api_key:
        return "", 0, []

    redact = helpers.get("redact_lifestyle_text") or helpers.get("redact_secrets") or (lambda s: s)
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# 微信读书阅读偏好、书架与精选想法/划线（仅提炼认知结论与阅读口味进 Tree，严禁搬运全书划线）",
        f"> 提取时间: {now_iso()} | 仅作外脑索引与认知提炼参考",
        "",
    ]
    pids: list[str] = []

    # 1. Overall reading profile
    st_rd, rd = client.gateway_call(api_key, "/readdata/detail", {"mode": "overall"})
    if st_rd == 200 and not rd.get("errcode"):
        total_sec = int(rd.get("totalReadTime") or 0)
        read_days = int(rd.get("readDays") or 0)
        pref_cat = str(rd.get("preferCategoryWord") or "").strip()
        pref_time = str(rd.get("preferTimeWord") or "").strip()
        lines.append("## 一、阅读统计与偏好画像")
        lines.append(f"- **累计阅读**：{total_sec // 3600} 小时 {(total_sec % 3600) // 60} 分钟（有效阅读 {read_days} 天）")
        if pref_cat or pref_time:
            lines.append(f"- **偏好特征**：{' · '.join(x for x in (pref_cat, pref_time) if x)}")
        cats = rd.get("preferCategory") if isinstance(rd.get("preferCategory"), list) else []
        if cats:
            cat_str = "、".join(
                f"{c.get('categoryTitle')}({int(c.get('readingCount') or 0)}本)"
                for c in cats[:6]
                if isinstance(c, dict) and c.get("categoryTitle")
            )
            if cat_str:
                lines.append(f"- **高频阅读分类**：{cat_str}")
        authors = rd.get("preferAuthor") if isinstance(rd.get("preferAuthor"), list) else []
        if authors:
            au_str = "、".join(
                f"{a.get('name')}({a.get('count')}本)"
                for a in authors[:6]
                if isinstance(a, dict) and a.get("name")
            )
            if au_str:
                lines.append(f"- **常读作者**：{au_str}")
        longest = rd.get("readLongest") if isinstance(rd.get("readLongest"), list) else []
        if longest:
            top_books = []
            for item in longest[:8]:
                if not isinstance(item, dict):
                    continue
                b = item.get("book") or item.get("albumInfo") or {}
                btitle = str(b.get("title") or b.get("name") or "").strip()
                rsec = int(item.get("readTime") or 0)
                if btitle:
                    top_books.append(f"《{btitle}》({rsec // 3600}h{(rsec % 3600) // 60}m)")
            if top_books:
                lines.append(f"- **阅读最久榜单**：{'、'.join(top_books)}")
        lines.append("")
        pids.append(short_hash(f"weread:overall:{total_sec}:{read_days}"))

    # 2. Notebooks overview
    notebooks: list[dict[str, Any]] = []
    last_sort: Any = None
    for _ in range(3):
        nb_params: dict[str, Any] = {"count": 30}
        if last_sort is not None:
            nb_params["lastSort"] = last_sort
        st_nb, nb = client.gateway_call(api_key, "/user/notebooks", nb_params)
        if st_nb != 200 or nb.get("errcode"):
            break
        batch = nb.get("books") if isinstance(nb.get("books"), list) else []
        if not batch:
            break
        notebooks.extend(b for b in batch if isinstance(b, dict))
        if not nb.get("hasMore"):
            break
        last_sort = batch[-1].get("sort")
        if last_sort is None:
            break

    if notebooks:
        notebooks.sort(key=lambda b: int(b.get("reviewCount") or 0) * 3 + int(b.get("noteCount") or 0), reverse=True)
        lines.append(f"## 二、有笔记/想法的书籍清单（共 {len(notebooks)} 本，建议同步目录至 `seed/外部上下文源.md`）")
        for b in notebooks[:25]:
            info = b.get("book") if isinstance(b.get("book"), dict) else {}
            bid = str(b.get("bookId") or info.get("bookId") or "")
            title = str(info.get("title") or bid).strip()
            author = str(info.get("author") or "").strip()
            rcnt = int(b.get("reviewCount") or 0)
            ncnt = int(b.get("noteCount") or 0)
            lines.append(f"- 《{redact(title)}》" + (f"（{author}）" if author else "") + f" · ID: `{bid}` · 想法 {rcnt} / 划线 {ncnt}")
            pids.append(short_hash(f"weread:nb:{bid}"))
        lines.append("")

        top_n = max(3, min(max_sessions // 3, 8))
        lines.append(f"## 三、重点书籍个人想法与精选划线（前 {min(top_n, len(notebooks))} 本）")
        for b in notebooks[:top_n]:
            info = b.get("book") if isinstance(b.get("book"), dict) else {}
            bid = str(b.get("bookId") or info.get("bookId") or "")
            title = str(info.get("title") or bid).strip()
            if not bid:
                continue
            book_lines: list[str] = []
            st_rv, rv = client.gateway_call(api_key, "/review/list/mine", {"bookid": bid, "count": 15, "synckey": 0})
            if st_rv == 200 and not rv.get("errcode"):
                for item in (rv.get("reviews") or [])[:10]:
                    r_obj = (item.get("review") or {}) if isinstance(item, dict) else {}
                    content = str(r_obj.get("content") or "").strip()
                    if content:
                        ch = str(r_obj.get("chapterName") or "").strip()
                        prefix = f"[{ch}] " if ch else ""
                        book_lines.append(f"  - [想法] {prefix}{redact(content[:360])}")
            st_bm, bm = client.gateway_call(api_key, "/book/bookmarklist", {"bookId": bid})
            if st_bm == 200 and not bm.get("errcode"):
                for item in (bm.get("updated") or [])[:10]:
                    if not isinstance(item, dict):
                        continue
                    mtxt = str(item.get("markText") or "").strip()
                    if mtxt:
                        book_lines.append(f"  - [划线] {redact(mtxt[:240])}")
            if book_lines:
                lines.append(f"### 《{redact(title)}》（`weread://reading?bId={bid}`）")
                lines.extend(book_lines)
                lines.append("")

    if not pids:
        return "", 0, []
    return "\n".join(lines).strip() + "\n", len(pids), pids
