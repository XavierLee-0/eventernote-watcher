from __future__ import annotations

import asyncio
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from .base import Notifier, register


@register
class EmailNotifier(Notifier):
    name = "email"
    label = "邮件 (SMTP)"

    def validate(self):
        for field in ("host", "username", "password", "from_addr", "to_addr"):
            if not self.config.get(field):
                return f"缺少 {field}"
        return None

    async def send(self, title: str, content: str) -> None:
        cfg = self.config
        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = Header(title, "utf-8")
        msg["From"] = formataddr(("Eventernote Watcher", cfg["from_addr"]))
        msg["To"] = cfg["to_addr"]

        def _send():
            if cfg.get("use_ssl", True):
                with smtplib.SMTP_SSL(cfg["host"], int(cfg.get("port", 465)), timeout=20) as s:
                    s.login(cfg["username"], cfg["password"])
                    s.sendmail(cfg["from_addr"], [cfg["to_addr"]], msg.as_string())
            else:
                with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=20) as s:
                    s.starttls()
                    s.login(cfg["username"], cfg["password"])
                    s.sendmail(cfg["from_addr"], [cfg["to_addr"]], msg.as_string())

        await asyncio.to_thread(_send)
