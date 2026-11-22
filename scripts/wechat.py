"""Water 连接器：微信（WeChat）闭环实现。

基于本机 `wechat-cli`：
1. 默认只提取高纯度的「微信收藏（favorites）」，严禁全量扫荡私聊/群聊流水；
2. 自动清洗微信公众号链接中的 `pass_ticket` / `uin` / `key` / `exportkey` 等个人追踪参数；
3. 未初始化时引导运行 `wechat-cli init` 提取本地密钥。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

from _bridge import redact_lifestyle_text

CONNECTOR_ID = "wechat"


def _find_wechat_cli() -> str | None:
    return shutil.which("wechat-cli")


def _is_initialized() -> bool:
    cfg = Path.home() / ".wechat-cli/config.json"
    dec = Path.home() / ".wechat-cli/decrypted"
    return cfg.is_file() and dec.is_dir()


def _run_wechat(args: list[str], timeout: int = 30, interactive: bool = False) -> tuple[int, str]:
    cli = _find_wechat_cli()
    if not cli:
        return 1, "未检测到 wechat-cli"
    try:
        if interactive:
            proc = subprocess.run([cli, *args], timeout=timeout)
            return proc.returncode, ""
        proc = subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout)
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return proc.returncode, out
    except Exception as e:
        return 1, str(e)


def _clean_wx_urls(text: str) -> str:
    """剥离公众号链接中的 pass_ticket/uin/key/exportkey 等敏感参数，只留核心文章标识。"""
    def _repl(m: re.Match) -> str:
        url = m.group(0)
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        keep = {k: v for k, v in qs.items() if k in {"__biz", "mid", "idx", "sn"}}
        new_q = urllib.parse.urlencode(keep, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_q))

    return re.sub(r"https?://mp\.weixin\.qq\.com/s\?[^\s\"'<>]+", _repl, text)


def discover() -> dict[str, Any]:
    cli = _find_wechat_cli()
    if not cli:
        return {
            "name": "微信 (收藏与定向协作决策)",
            "available": False,
            "detail": "未检测到 wechat-cli（请先安装 wechat-cli）",
        }
    if not _is_initialized():
        return {
            "name": "微信 (收藏与定向协作决策)",
            "available": False,
            "detail": f"检测到 {cli}，但尚未初始化（可运行 `water login wechat` 执行 `wechat-cli init`）",
        }
    return {
        "name": "微信 (收藏与定向协作决策)",
        "available": True,
        "detail": f"已就绪（{cli}，默认仅读取微信收藏）",
    }


def login_qr(timeout: int = 180) -> bool:
    if not _find_wechat_cli():
        return False
    code, _ = _run_wechat(["init"], timeout=timeout, interactive=True)
    return code == 0 and _is_initialized()


def extract(days: int, max_sessions: int, helpers: dict[str, Any]) -> tuple[str, int, list[str]]:
    if not _find_wechat_cli() or not _is_initialized():
        return "", 0, []

    limit = max(1, min(max_sessions, 30))
    code, out = _run_wechat(["favorites", "--limit", str(limit)], timeout=30)
    if code != 0 or not out.strip():
        code, out = _run_wechat(["favorites"], timeout=30)
        if code != 0 or not out.strip():
            return "", 0, []

    redact = helpers.get("redact_secrets")
    short_hash = helpers.get("short_hash", lambda s: s[:12])
    now_iso = helpers.get("now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    lines = [
        "# 微信收藏精华（默认不扫私聊与群聊流水，仅提取主动收藏的高纯度内容）",
        f"> 提取时间: {now_iso()}",
        "",
    ]
    pids: list[str] = []

    try:
        data = json.loads(out)
        favs = data.get("favorites") if isinstance(data, dict) else None
    except Exception:
        favs = None

    if isinstance(favs, list):
        for item in favs[:limit]:
            fid = str(item.get("id") or "")
            ftype = item.get("type") or "收藏"
            ftime = str(item.get("time") or "")[:10]
            summary = _clean_wx_urls(str(item.get("summary") or "").strip())
            summary = redact_lifestyle_text(summary, redact)
            if not summary:
                continue
            lines.append(f"- [{ftime or '未知日期'}]（{ftype}）{summary}")
            if fid:
                pids.append(short_hash(f"wechat:fav:{fid}"))
        if not pids:
            return "", 0, []
        return "\n".join(lines) + "\n", len(pids), pids

    cleaned = redact_lifestyle_text(_clean_wx_urls(out), redact)
    lines.append(cleaned)
    return "\n".join(lines) + "\n", 1, [short_hash(f"wechat:favorites:{len(cleaned)}")]
