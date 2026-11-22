"""Water 共享桥接辅助模块：统一定位本地套件、执行只读命令与敏感信息脱敏。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def find_toolkit_cli(skill_names: list[str], env_var: str = "") -> Path | None:
    """按优先级定位本机已安装的外部平台 cli.py。"""
    if env_var:
        p_env = os.environ.get(env_var, "").strip()
        if p_env and Path(p_env).is_file():
            return Path(p_env)

    home = Path.home()
    roots = [
        home / ".agents/skills",
        home / ".gemini/config/skills",
        home / ".gemini/antigravity/scratch",
        home / "Desktop",
    ]
    for r in roots:
        for name in skill_names:
            cand = r / name / "cli.py"
            if cand.is_file():
                return cand
    return None


def run_cli(
    cli: Path,
    args: list[str],
    timeout: int = 30,
    interactive: bool = False,
) -> tuple[int, str]:
    """执行指定 cli.py 子命令。"""
    py = shutil.which("python3") or "python3"
    cmd = [py, str(cli), *args]
    try:
        if interactive:
            proc = subprocess.run(cmd, cwd=str(cli.parent), timeout=timeout)
            return proc.returncode, ""
        proc = subprocess.run(
            cmd,
            cwd=str(cli.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return proc.returncode, out
    except Exception as e:
        return 1, str(e)


def redact_lifestyle_text(text: str, extra_redact=None) -> str:
    """脱敏手机号、身份证、金额、Token 等隐私字段，只保留供偏好提炼的语义线索。"""
    if extra_redact:
        text = extra_redact(text)
    # 脱敏中国大陆手机号
    text = re.sub(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)", r"\1****\2", text)
    # 脱敏身份证号
    text = re.sub(r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)", "[REDACTED_ID]", text)
    # 脱敏常见密钥/Token
    text = re.sub(r"(sk-[A-Za-z0-9_-]{16,})", "[REDACTED_KEY]", text)
    text = re.sub(r"(gh[pou]_[A-Za-z0-9]{16,})", "[REDACTED_TOKEN]", text)
    return text


def standard_discover(
    name: str,
    skill_names: list[str],
    status_args: list[str],
    env_var: str = "",
) -> dict[str, Any]:
    """通用状态探测：检查 cli.py 是否存在并快速校验当前登录态。"""
    cli = find_toolkit_cli(skill_names, env_var=env_var)
    if not cli:
        return {
            "name": name,
            "available": False,
            "detail": f"未在本机检测到 {' / '.join(skill_names)} 工具套件",
        }
    code, out = run_cli(cli, status_args, timeout=15)
    lower = out.lower()
    not_logged = any(
        kw in lower
        for kw in ["未登录", "not logged", "login required", "请先登录", "无效", "expired", "cookie"]
    )
    if code == 0 and out and not not_logged:
        return {
            "name": name,
            "available": True,
            "detail": f"已就绪（{cli}）",
        }
    return {
        "name": name,
        "available": False,
        "detail": f"工具已就绪但未登录（{cli}），可运行 `water login` 登录",
    }
