"""Water 标准只读连接器模板（对接 Tree `.tree/seed/connectors/*.py`）。

使用方式：
1. 复制本文件为 `<app_id>.py` 并修改 `CONNECTOR_ID`；
2. 实现 `discover()` 与 `extract(days, max_sessions, helpers)`；
3. 确保只做只读拉取与脱敏，不向外部平台写入或修改任何状态。
"""

from __future__ import annotations

from typing import Any

CONNECTOR_ID = "example"


def discover() -> dict[str, Any]:
    """向 `tree seed scan` 报告当前连接器是否在本机可用。"""
    return {
        "name": "示例外部数据源",
        "available": False,
        "detail": "模板文件，请复制并替换为真实探测逻辑",
    }


def extract(
    days: int,
    max_sessions: int,
    helpers: dict[str, Any],
) -> tuple[str, int, list[str]]:
    """只读提取外部数据并输出脱敏后的高信噪比 Markdown。

    Args:
        days: 回溯天数窗口。
        max_sessions: 最大拉取条目/会话数。
        helpers: Tree 提供的工具集，包含 `redact_secrets`, `clean_chat_text`,
                 `now_iso`, `short_hash`, `within_days` 等。

    Returns:
        (markdown_text, item_count, processed_ids)
    """
    redact = helpers.get("redact_secrets", lambda s: s)
    now_iso = helpers.get("now_iso", lambda: "")

    lines = [
        f"# {CONNECTOR_ID} 外部上下文精华",
        f"> 提取时间: {now_iso()}",
        "",
        "- （在此写入提炼后的高信噪比结论，禁止倾倒原始流水）",
    ]
    md = redact("\n".join(lines))
    return md, 0, []
