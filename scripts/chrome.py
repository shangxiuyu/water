"""Water 连接器：Google Chrome 书签与高频浏览线索（零依赖标准库实现）。

复用 Chrome `Local State` 多 Profile 发现逻辑：
1. 仅只读读取 `Bookmarks`（明文 JSON）与 `History`（复制临时副本只读 SQLite）；
2. 严禁读取 `Cookies`、`Login Data`、`Local Storage` 等凭证文件；
3. 自动过滤 localhost、搜索跳转页、登录回调页，剥离 URL 查询参数，仅输出高频访问站点与书签。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

from _bridge import redact_lifestyle_text

CONNECTOR_ID = "chrome"

# Chrome 时间戳起点：1601-01-01 00:00:00 UTC（微秒）
CHROME_EPOCH_OFFSET_US = 11644473600 * 1_000_000

SKIP_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "accounts.google.com",
    "login.microsoftonline.com",
}
SKIP_HOST_KEYWORDS = ("oauth", "sso.", "auth.", "login.")


def _chrome_user_data_dir() -> Path | None:
    home = Path.home()
    if sys.platform == "darwin":
        p = home / "Library/Application Support/Google/Chrome"
    elif sys.platform.startswith("linux"):
        p = home / ".config/google-chrome"
    else:
        p = home / "AppData/Local/Google/Chrome/User Data"
    return p if p.is_dir() else None


def _list_profiles(chrome_dir: Path) -> list[dict[str, Any]]:
    info_cache: dict[str, Any] = {}
    ls_path = chrome_dir / "Local State"
    if ls_path.is_file():
        try:
            raw = json.loads(ls_path.read_text(encoding="utf-8"))
            ic = ((raw.get("profile") or {}).get("info_cache")) if isinstance(raw, dict) else None
            if isinstance(ic, dict):
                info_cache = ic
        except Exception:
            pass

    cand_ids = set(info_cache.keys())
    try:
        for entry in chrome_dir.iterdir():
            if entry.is_dir() and (entry.name == "Default" or entry.name.startswith("Profile ")):
                cand_ids.add(entry.name)
    except Exception:
        pass

    profiles: list[dict[str, Any]] = []
    for pid in cand_ids:
        pdir = chrome_dir / pid
        hist = pdir / "History"
        bmk = pdir / "Bookmarks"
        if not hist.is_file() and not bmk.is_file():
            continue
        meta = info_cache.get(pid) if isinstance(info_cache.get(pid), dict) else {}
        email = str(meta.get("user_name") or "").strip()
        name = str(meta.get("name") or pid).strip()
        profiles.append({
            "id": pid,
            "name": name,
            "email": email,
            "dir": pdir,
            "has_history": hist.is_file(),
            "has_bookmarks": bmk.is_file(),
        })

    profiles.sort(key=lambda x: (0 if x["email"] else 1, 0 if x["id"] == "Default" else 1, x["id"]))
    return profiles


def _clean_url(url: str) -> tuple[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.hostname or ""
    if not host or host in SKIP_HOSTS or any(k in host for k in SKIP_HOST_KEYWORDS):
        return None
    # 过滤搜索引擎结果页
    if (host.endswith("google.com") or host == "www.baidu.com" or host == "cn.bing.com") and parsed.path in {"/search", "/s"}:
        return None
    clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
    return host, clean


def _collect_bookmarks(bmk_path: Path, max_items: int) -> list[str]:
    if not bmk_path.is_file():
        return []
    try:
        data = json.loads(bmk_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    results: list[str] = []

    def walk(node: Any, folder: str = "") -> None:
        if len(results) >= max_items or not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == "url":
            title = str(node.get("name") or "").strip()
            url = str(node.get("url") or "").strip()
            cleaned = _clean_url(url)
            if title and cleaned:
                _, clean_u = cleaned
                prefix = f"[{folder}] " if folder else ""
                results.append(f"- {prefix}{title} ({clean_u})")
        elif ntype == "folder":
            fname = str(node.get("name") or "").strip()
            next_folder = f"{folder}/{fname}" if (folder and fname) else (fname or folder)
            for child in node.get("children") or []:
                walk(child, next_folder)

    roots = data.get("roots") or {}
    if isinstance(roots, dict):
        for root_node in roots.values():
            walk(root_node)
    return results


def _collect_history(hist_path: Path, days: int, max_items: int) -> tuple[list[tuple[str, int]], list[str]]:
    if not hist_path.is_file():
        return [], []

    cutoff_us = int((time.time() - days * 86400) * 1_000_000) + CHROME_EPOCH_OFFSET_US
    tmp_dir = Path(tempfile.mkdtemp(prefix="water_chrome_"))
    tmp_db = tmp_dir / "History"
    host_counts: Counter[str] = Counter()
    pages: list[str] = []
    seen_urls: set[str] = set()

    try:
        shutil.copy2(hist_path, tmp_db)
        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT url, title, visit_count
            FROM urls
            WHERE last_visit_time >= ? AND visit_count >= 3 AND title != ''
            ORDER BY visit_count DESC, last_visit_time DESC
            LIMIT 250
            """,
            (cutoff_us,),
        )
        for url, title, vcount in cur.fetchall():
            cleaned = _clean_url(str(url or ""))
            if not cleaned:
                continue
            host, clean_u = cleaned
            host_counts[host] += int(vcount or 1)
            if clean_u not in seen_urls and len(pages) < max_items:
                seen_urls.add(clean_u)
                t = str(title or "").strip().replace("\n", " ")[:80]
                pages.append(f"- ({vcount}次) {t} — {clean_u}")
        conn.close()
    except Exception:
        pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return host_counts.most_common(12), pages


def discover() -> dict[str, Any]:
    chrome_dir = _chrome_user_data_dir()
    if not chrome_dir:
        return {
            "name": "Google Chrome (书签与高频站点)",
            "available": False,
            "detail": "未检测到本机 Google Chrome 数据目录",
        }
    profiles = _list_profiles(chrome_dir)
    if not profiles:
        return {
            "name": "Google Chrome (书签与高频站点)",
            "available": False,
            "detail": "检测到 Chrome 目录但无可用的书签/历史记录 Profile",
        }
    labels = [f"{p['name']}({p['email']})" if p["email"] else p["name"] for p in profiles[:3]]
    return {
        "name": "Google Chrome (书签与高频站点)",
        "available": True,
        "detail": f"检测到 {len(profiles)} 个配置：{', '.join(labels)}",
    }


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    chrome_dir = _chrome_user_data_dir()
    if not chrome_dir:
        return "", 0, []
    profiles = _list_profiles(chrome_dir)
    if not profiles:
        return "", 0, []

    redact = helpers.get("redact_secrets")
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    limit = max(5, min(max_sessions, 30))
    lines = [
        "# Google Chrome 书签与高频访问线索（仅用于提炼常用工具、技术栈与关注领域，严禁流水入树）",
        f"> 提取时间: {now_iso()} | 回溯窗口: 近 {days} 天 | 不读取 Cookie 与密码",
        "",
    ]

    total_items = 0
    for p in profiles[:2]:
        label = f"{p['name']} ({p['email']})" if p["email"] else p["name"]
        top_hosts, top_pages = _collect_history(p["dir"] / "History", days=days, max_items=limit)
        bookmarks = _collect_bookmarks(p["dir"] / "Bookmarks", max_items=limit)
        if not top_hosts and not top_pages and not bookmarks:
            continue

        lines.append(f"## Profile: {label}")
        if top_hosts:
            lines.append("### 高频访问域名（工具链与注意力分布）")
            lines.append(", ".join(f"`{h}`({c})" for h, c in top_hosts))
            lines.append("")
        if top_pages:
            lines.append("### 高频回访页面")
            lines.extend(top_pages)
            lines.append("")
            total_items += len(top_pages)
        if bookmarks:
            lines.append("### 书签精选")
            lines.extend(bookmarks)
            lines.append("")
            total_items += len(bookmarks)

    if total_items == 0:
        return "", 0, []

    md = redact_lifestyle_text("\n".join(lines).strip() + "\n", redact)
    return md, total_items, [short_hash(f"chrome:{total_items}")]
