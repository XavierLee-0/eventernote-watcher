from __future__ import annotations

import httpx

from .base import Notifier, register


@register
class WxPusherNotifier(Notifier):
    name = "wxpusher"
    label = "WxPusher"

    def validate(self):
        if not self.config.get("app_token"):
            return "缺少 app_token"
        if not self.config.get("uid"):
            return "缺少 uid（关注公众号后在用户页获取）"
        return None

    async def send(self, title: str, content: str, html: str | None = None) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://wxpusher.zjiecode.com/api/send/message",
                json={
                    "appToken": self.config["app_token"],
                    "content": content,
                    "summary": title[:99],
                    "contentType": 3,  # markdown
                    "uids": [self.config["uid"]],
                },
            )
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"WxPusher: {data.get('msg')}")
