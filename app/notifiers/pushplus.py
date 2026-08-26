from __future__ import annotations

import httpx

from .base import Notifier, register


@register
class PushPlusNotifier(Notifier):
    name = "pushplus"
    label = "PushPlus"

    def validate(self):
        if not self.config.get("token"):
            return "缺少 token"
        return None

    async def send(self, title: str, content: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://www.pushplus.plus/send",
                json={
                    "token": self.config["token"],
                    "title": title,
                    "content": content,
                    "template": "markdown",
                },
            )
            data = resp.json()
            if data.get("code") != 200:
                raise RuntimeError(f"PushPlus: {data.get('msg')}")
