"""将 events_cache 中的活动输出为 iCal (RFC 5545) 日历。"""
from __future__ import annotations

from datetime import datetime, timezone

from .db import Database


def _escape(text: str) -> str:
    """转义 iCal 文本值中的特殊字符。"""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """超过 75 字节(按 utf-8)的行折行。"""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not parts else 74  # 折行开头占 1 个空格
        if len(cur) + len(b) > limit:
            parts.append(cur.decode("utf-8"))
            cur = b
        else:
            cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def generate_ics(db: Database, include_past: bool = False) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//eventernote-watcher//CN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Eventernote 活动",
    ]
    rows = db.list_events(future_only=not include_past)
    # 同一场活动可能由多位监视中的出演者共同出演:
    # 按 event_id 去重合并, UID 只用 event_id, 出演者名并入描述
    by_event: dict[int, list[dict]] = {}
    for ev in rows:
        by_event.setdefault(ev["event_id"], []).append(ev)
    for event_id, group in by_event.items():
        ev = group[0]
        actors = "、".join(g["actor_name"] for g in group)
        uid = f"{event_id}@eventernote-watcher"
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{now}")
        if ev["date"]:
            # 活动只有日期粒度(开演时间在说明里), 输出为全天事件
            lines.append(f"DTSTART;VALUE=DATE:{ev['date'].replace('-', '')}")
            lines.append(f"DTEND;VALUE=DATE:{ev['date'].replace('-', '')}")
        desc = [f"出演者: {actors}", ev["url"]]
        if ev["open_time"]:
            desc.append(ev["open_time"])
        lines.append(_fold(f"DESCRIPTION:{_escape(chr(10).join(desc))}"))
        lines.append(_fold(f"SUMMARY:{_escape(ev['name'])}"))
        if ev["place"]:
            lines.append(_fold(f"LOCATION:{_escape(ev['place'])}"))
        lines.append(f"URL:{ev['url']}")
        # 提醒: 全天事件 DTSTART 是当天 00:00, 提前 10 小时即前一日 14:00
        # 注意 Google Calendar 对订阅日历会忽略 VALARM, 需在其日历设置里配置通知
        lines.append("BEGIN:VALARM")
        lines.append("ACTION:DISPLAY")
        lines.append(f"DESCRIPTION:{_escape(ev['name'])}")
        lines.append("TRIGGER:-PT10H")
        lines.append("END:VALARM")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
